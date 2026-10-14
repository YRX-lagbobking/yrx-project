"""
Risk 节点：三层风险检测与分级（FR-06 / 5.7 / AC-08~AC-10）—— 本项目的核心节点

关键设计（务必读懂，答辩必问）：
    1. **等级以代码判定为准**：`risk_level_of(anomalies)` 是纯计算层的结论，
       LLM 只被允许"把异常描述润色成人话"，**绝不允许修改等级**。
       代码里在模型调用之后再次用 `code_level` 覆盖 `risk_level`，
       从实现层面堵死"模型篡改级别"的可能（注释即契约）。
    2. **纯计算层无副作用**（AC-10）：`detect_anomaly` 只读 InfluxDB + 计算，
       不写库、不发通知、不改状态；写 alert_record 与通知只发生在 notify 节点。
    3. 三层检测由 anomaly_detector 内部完成：规则阈值（高危）/
       统计趋势（近 7 天偏离 >30%，连续 3 天同向升级中危）/ 个体离群（MAD）。
    4. `need_notify = risk_level in ("高危", "中危")`：高危立即通知、中危汇总后通知（降低误报噪音）。

写入状态：risk_level、risk_reasons、anomalies、need_notify、task_results["risk"]、health_step="risk"。
"""
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.messages import AIMessage

from app.ai.agent.multi_agent.schema.risk_schema import RiskSchema
from app.ai.agent.multi_agent.state.health_state import HealthState
from app.ai.model.my_model import MyModel, model_fallback_middleware
from app.ai.prompt.bulider_prompt import BuilderPromptYaml
from app.core.detect.anomaly_detector import detect_anomaly, risk_level_of
from app.core.logger import logger

# 读取外部提示词（模块顶层加载）
prompt = BuilderPromptYaml.get_prompt("risk_node.yaml")

# 需要通知家属的等级（高危立即通知、中危汇总通知）
NOTIFY_LEVELS = ("高危", "中危")
# 等级白名单（代码判定结果必须落在其中，否则按"正常"处理）
LEVELS = ("高危", "中危", "低危", "正常")


def _extract_question(state: HealthState) -> str:
    """取本轮用户问题：最后一条 HumanMessage（兜底取最后一条消息内容）"""
    messages = state.get("messages") or []
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content or "")
    if messages:
        return str(getattr(messages[-1], "content", "") or "")
    return ""


def _describe(anomalies: list[dict]) -> list[str]:
    """逐条取异常项的 description 作为风险原因（缺失时用 type/metric 拼一句兜底）"""
    reasons: list[str] = []
    for item in anomalies:
        if not isinstance(item, dict):
            continue
        description = item.get("description")
        if not description:
            description = f"指标 {item.get('metric', '未知')} 出现异常（{item.get('type', 'unknown')}）"
        reasons.append(str(description))
    return reasons


async def _polish(reasons: list[str]) -> list[str]:
    """
    可选润色：让模型把风险原因改写成家属能听懂的话。

    **只润色 reasons，不采纳模型给出的 level**（等级永远来自 risk_level_of）。
    任何失败都返回原始 reasons —— 模型不可用绝不影响风险结论。
    说明：这里用同步 `invoke`，教学/演示规模够用；生产建议换成 `ainvoke` 以免阻塞事件循环。
    """
    if not reasons:
        return []
    try:
        agent = create_agent(
            model=MyModel.get_model(),
            middleware=model_fallback_middleware(),
            system_prompt=prompt,
            response_format=RiskSchema,
        )
        user_text = "请把下面这些检测结论改写成家属能听懂的话，逐条对应，不要增删条目：\n" + "\n".join(reasons)
        result = await agent.ainvoke({"messages": [HumanMessage(content=user_text)]})
        schema = result.get("structured_response") if isinstance(result, dict) else None
        polished = [str(x) for x in (getattr(schema, "reasons", None) or [])]
        # 条数对不上说明模型改写时增删了条目 → 弃用，避免明细丢失或错位
        if len(polished) != len(reasons):
            logger.warning("[Risk] 润色结果条数与异常条数不一致，保留原始描述")
            return reasons
        return polished
    except Exception as e:
        logger.warning(f"[Risk] 风险原因润色失败，保留原始描述：{e}")
        return reasons


async def risk_node(state: HealthState):
    """风险判断节点：三层检测 → 分级 → 是否需要通知"""
    user_id = str(state.get("user_id") or "")
    anomalies: list[dict] = []
    risk_level = "正常"
    risk_reasons: list[str] = []
    try:
        logger.info(f"[Risk] 开始风险检测（纯计算层，mode=manual，无副作用）user_id={user_id}")
        # 1. 纯计算层检测：只读 + 计算，不写库、不发通知（mode 只影响日志，不影响检测逻辑）
        anomalies = detect_anomaly(user_id, mode="manual") or []
        # 2. 代码判定等级（唯一事实来源）
        code_level = risk_level_of(anomalies)
        risk_level = code_level if code_level in LEVELS else "正常"
        # 3. 风险原因逐条来自 anomaly["description"]
        risk_reasons = _describe(anomalies)
        # 4. 是否需要通知家属（高危立即、中危汇总）
        need_notify = risk_level in NOTIFY_LEVELS

        # 5. 可选：模型只润色措辞（不参与定级）
        risk_reasons = await _polish(risk_reasons)

        # 6. 再用代码结论覆盖一次等级与通知标记：
        #    模型环节不管返回什么，等级都不会被改（注释即契约）
        risk_level = code_level if code_level in LEVELS else "正常"
        need_notify = risk_level in NOTIFY_LEVELS

        note = (
            f"风险检测完成：等级={risk_level}，命中异常 {len(anomalies)} 项，需通知家属={need_notify}。"
            + ("原因：" + "；".join(risk_reasons) if risk_reasons else "")
        )
        logger.info(f"[Risk] {note}")
        return {
            "risk_level": risk_level,
            "risk_reasons": risk_reasons,
            "anomalies": anomalies,
            "need_notify": need_notify,
            "task_results": {
                "risk": {
                    "status": "ok",
                    "risk_level": risk_level,
                    "risk_reasons": risk_reasons,
                    "anomalies": anomalies,
                    "need_notify": need_notify,
                }
            },
            "health_step": "risk",
            "messages": [AIMessage(content=note)],
        }
    except Exception as e:
        # 异常：写 error 让 Manager 自动重试；同时给安全默认值
        # 说明：检测失败时等级按"正常"处理（不误报高危），但 error 状态会被 Manager 记录并重试，不会静默丢失
        logger.error(f"[Risk] 风险检测失败：{e}")
        return {
            "risk_level": "正常",
            "risk_reasons": [],
            "anomalies": [],
            "need_notify": False,
            "task_results": {"risk": {"status": "error", "msg": str(e)}},
            "health_step": "risk",
            "messages": [AIMessage(content="风险检测暂时不可用，我会再试一次；如您感觉不适请立刻联系家属或拨打 120。")],
        }


if __name__ == "__main__":
    # 自测说明：只验证纯函数（异常描述提取），不连模型、不连数据库。
    # detect_anomaly 是纯计算层，AC-08 可用"注入心率 140"的方式单独单测。
    demo = [
        {
            "type": "heart_rate_high",
            "level": "高危",
            "value": 140,
            "metric": "heart_rate",
            "description": "心率达到 140 次/分，超过安全范围",
            "source": "rule",
        }
    ]
    print("原因提取：", _describe(demo))
    print("提示词前 120 字：", prompt[:120].replace("\n", " "))
    # 真跑（需要 InfluxDB 有数据）：
    # import asyncio
    # async def test():
    #     print(await risk_node({"user_id": "u001", "messages": []}))
    # asyncio.run(test())
