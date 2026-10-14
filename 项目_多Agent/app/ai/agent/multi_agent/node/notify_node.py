"""
Notify 节点：分级预警与双通道通知（FR-07 / 5.7 / AC-11、AC-12）

重要边界（与 risk 节点配合，答辩必问）：
    `detect_anomaly` 是**纯计算层**，绝不写库、不发通知（AC-10）；
    **写 alert_record 和发起通知只发生在本节点**——副作用集中在一处，便于审计与重试。

三级内容组织（notify_node.yaml 规定写法）：
    ① 异常描述：来自 state["anomalies"] 的 description（技术事实，不加戏）；
    ② RAG 建议：来自 state["knowledge"]（带来源/版本/页码，可溯源）；
    ③ 通知文案：模型按 YAML 规则压缩成家属一眼能看懂的短句（失败走代码模板）。

两条通道（先家属、后老人端）：
    通道 1 家属：`notify_family_async(user_id, level, content)`（内部 Celery `.delay()` 异步投递）——**统一发邮件**（短信通道已删除）；
    通道 2 老人：`ws_manager.send_to_user(user_id, {...})` 推送 WebSocket（前端弹窗 + 语音播报）。

通道失败重试：`notify_family` 内部由 Celery 重试（max_retries=3 / 60s）。

容错要求：通知通道任何失败都不能抛异常；写 alert_record 失败也要能返回可用结论。
写入状态：notify_channel、notify_result、task_results["notify"]、health_step="notify"。
"""
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.messages import AIMessage

from app.ai.agent.multi_agent.state.health_state import HealthState
from app.ai.model.my_model import MyModel, model_fallback_middleware
from app.ai.prompt.bulider_prompt import BuilderPromptYaml
from app.core.config import settings
from app.core.logger import logger
from app.dao.alert_dao import AlertDAO

# 读取外部提示词（模块顶层加载）
prompt = BuilderPromptYaml.get_prompt("notify_node.yaml")

# 需要通知的等级（统一走邮件；短信通道已删除）
NOTIFY_LEVELS = ("高危", "中危")
CHANNEL_MAP = {"高危": "email", "中危": "email"}


def _extract_anomalies(state: HealthState) -> tuple[list[dict], list[str], str]:
    """取异常明细、风险原因、风险等级（异常明细优先用 risk 结果，其次用状态字段）"""
    task_results = state.get("task_results") or {}
    risk_result = task_results.get("risk") if isinstance(task_results.get("risk"), dict) else {}
    anomalies = state.get("anomalies") or risk_result.get("anomalies") or []
    reasons = state.get("risk_reasons") or risk_result.get("risk_reasons") or []
    level = state.get("risk_level") or risk_result.get("risk_level") or "正常"
    return list(anomalies), [str(r) for r in reasons], str(level)


def _template_notify(level: str, reasons: list[str], knowledge: list[dict]) -> str:
    """
    代码模板通知文案（模型不可用时的兜底，保证通知"一定能发出去"）。
    结构：等级 → 异常要点 → 处置建议（带来源）→ 行动要求。
    """
    lines = [f"【{level}健康预警】家中老人体征出现异常，请及时关注。"]
    if reasons:
        lines.append("异常要点：" + "；".join(reasons[:3]))
    else:
        lines.append("异常要点：检测到体征偏离安全范围，具体指标请查看系统告警列表。")
    if knowledge:
        tips = [str(item.get("content") or "") for item in knowledge[:2]]
        source = knowledge[0].get("source") or "知识库"
        lines.append("处置建议：" + "；".join(t for t in tips if t) + f"（依据：{source}）")
    else:
        lines.append("处置建议：请电话确认老人状态；若出现胸闷、呼吸困难、意识不清，立即拨打 120。")
    lines.append("请尽快回电确认，并在系统中确认本条告警。")
    return "\n".join(lines)


async def _polish(level: str, reasons: list[str], knowledge: list[dict]) -> str:
    """
    模型润色通知文案（按 notify_node.yaml 的"给家属的简洁通知"规则）。
    失败返回模板文案；返回空同样回退模板。
    """
    template = _template_notify(level, reasons, knowledge)
    try:
        agent = create_agent(model=MyModel.get_model(), middleware=model_fallback_middleware(), system_prompt=prompt)
        user_text = (
            f"风险等级：{level}\n"
            f"异常明细：{reasons}\n"
            f"知识库建议：{knowledge}\n"
            "请按规则生成一条发给家属的简洁通知（120 字以内）。"
        )
        result = await agent.ainvoke({"messages": [HumanMessage(content=user_text)]})
        content = ""
        if isinstance(result, dict):
            messages = result.get("messages") or []
            if messages:
                content = str(getattr(messages[-1], "content", "") or "")
        content = content.strip()
        return content or template
    except Exception as e:
        logger.error(f"[Notify] 通知文案润色失败，使用模板文案：{e}")
        return template


def _save_alert(user_id: str, anomalies: list[dict], level: str, description: str) -> None:
    """写一条 alert_record（source="manual"：本次是老人主动问询触发的检测，不是自动巡检）"""
    try:
        first = anomalies[0] if anomalies else {}
        alert_type = str(first.get("type") or "vitals_abnormal")
        value = str(first.get("value") if first.get("value") is not None else "")
        AlertDAO.save(user_id, alert_type, level, value, description, source="manual")
        logger.info(f"[Notify] 告警已写入 alert_record：type={alert_type} level={level} value={value}")
    except Exception as e:
        # 写库失败不影响通知（通知价值高于记录完整性），但必须留日志
        logger.error(f"[Notify] 告警写库失败（不影响通知）：{e}")


def _notify_family(user_id: str, level: str, content: str, channel: str) -> dict:
    """通道 1：家属通知（Celery 异步任务投递；同步函数）"""
    try:
        from app.core.task.notify_task import notify_family_async

        result = notify_family_async(user_id, level, content, channel)
        logger.info(f"[Notify] 家属通知已提交：{result}")
        return result if isinstance(result, dict) else {"ok": True, "raw": str(result)}
    except Exception as e:
        logger.error(f"[Notify] 家属通知提交异常（已降级，不抛出）：{e}")
        return {"ok": False, "msg": str(e)}


async def _notify_elder_ws(user_id: str, level: str, message: str, action: str = "voice_play") -> bool:
    """通道 2：推送老人端 WebSocket（含 RAG 处置建议），前端弹窗 + 自动语音播报"""
    try:
        from app.web.websocker_router.websocket_notify import ws_manager

        ok = await ws_manager.send_to_user(
            user_id,
            {"type": "health_alert", "level": level, "message": message, "action": action},
        )
        logger.info(f"[Notify] 老人端 WebSocket 推送结果：{ok}")
        return bool(ok)
    except Exception as e:
        logger.error(f"[Notify] 老人端 WebSocket 推送失败（已降级，不抛出）：{e}")
        return False


async def notify_node(state: HealthState):
    """通知节点：分级 + 双通道 + 人工确认兜底"""
    user_id = str(state.get("user_id") or "")
    notify_channel = ""
    notify_result = ""
    need_human_confirm = False
    try:
        anomalies, risk_reasons, risk_level = _extract_anomalies(state)
        knowledge = state.get("knowledge") or []
        need_notify = bool(state.get("need_notify")) or risk_level in NOTIFY_LEVELS
        logger.info(f"[Notify] level={risk_level} need_notify={need_notify} 异常={len(anomalies)} 知识={len(knowledge)}")

        # 1. 不需要通知：直接返回（task_results 仍记 ok，避免 Manager 误判为失败重试）
        if not need_notify:
            note = f"当前风险等级为{risk_level}，无需通知家属。"
            return {
                "notify_channel": "",
                "notify_result": note,
                "task_results": {"notify": {"status": "ok", "level": risk_level, "notified": False, "channel": ""}},
                "health_step": "notify",
                "messages": [AIMessage(content=note)],
            }

        # 2. 生成通知文案（模型润色 → 模板兜底）
        content = await _polish(risk_level, risk_reasons, knowledge)
        # 3. 写告警记录（副作用只在本节点发生，source="manual"）
        _save_alert(user_id, anomalies, risk_level, content)

        # 4. 通道 1：家属邮件（Celery 异步投递；短信通道已删除）
        channel = CHANNEL_MAP.get(risk_level, "email")
        family_result = _notify_family(user_id, risk_level, content, channel)
        notify_channel = channel
        if isinstance(family_result, dict) and family_result.get("ok") is False:
            notify_result = f"家属通知投递失败（{family_result.get('msg', '未知原因')}）"
        else:
            notify_result = f"已通过{channel}通知家属"

        # 6. 通道 2：老人端 WebSocket（无论上一步是否人工确认，老人端都要即时提示）
        ws_ok = await _notify_elder_ws(user_id, risk_level, content)
        if ws_ok:
            notify_result += "；老人端已推送提醒"
        else:
            notify_result += "；老人端未在线，提醒将在下次连接后展示"

        return {
            "notify_channel": notify_channel,
            "notify_result": notify_result,
            "need_human_confirm": need_human_confirm,
            "task_results": {
                "notify": {
                    "status": "ok",
                    "level": risk_level,
                    "notified": True,
                    "channel": notify_channel,
                    "content": content,
                    "result": notify_result,
                    "need_human_confirm": need_human_confirm,
                    "ws_pushed": ws_ok,
                }
            },
            "health_step": "notify",
            "messages": [AIMessage(content=f"通知处理完成：{notify_result}")],
        }
    except Exception as e:
        # 异常：写 error 让 Manager 重试；通知节点"无论成败都不能抛异常"（本函数顶层兜住）
        logger.error(f"[Notify] 通知处理失败：{e}")
        return {
            "notify_channel": notify_channel,
            "notify_result": notify_result or "通知流程异常，已记录，请家属主动确认老人状态",
            "need_human_confirm": need_human_confirm,
            "task_results": {"notify": {"status": "error", "msg": str(e)}},
            "health_step": "notify",
            "messages": [AIMessage(content="通知流程出现异常，已记录日志；请家属尽快主动联系老人确认状态。")],
        }


if __name__ == "__main__":
    # 自测说明：只验证纯函数（模板通知文案 / 异常提取），不连 Celery、Redis、WebSocket。
    demo_anomalies = [
        {
            "type": "heart_rate_high",
            "level": "高危",
            "value": 140,
            "metric": "heart_rate",
            "description": "心率达到 140 次/分，超过安全范围",
            "source": "rule",
        }
    ]
    print("模板通知：")
    print(_template_notify("高危", ["心率达到 140 次/分，超过安全范围"], [{"content": "立即静坐休息并测量脉搏", "source": "心血管急救手册"}]))
    print("异常提取：", _extract_anomalies({"task_results": {"risk": {"anomalies": demo_anomalies, "risk_level": "高危"}}}))
    # 真跑（需要 Redis/Celery 或已配置的降级路径）：
    # import asyncio
    # async def test():
    #     state = {"user_id": "u001", "messages": [], "risk_level": "中危", "need_notify": True, "anomalies": demo_anomalies}
    #     print(await notify_node(state))
    # asyncio.run(test())
