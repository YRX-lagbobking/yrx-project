"""
Remind 节点：用药提醒生成（FR-04 / 5.4 / AC-05）

流程：
    1. `MedicationDAO.list_today(user_id)` 取今日用药记录（含"待服/已服/漏服"状态）；
    2. `MedicationDAO.list_plan(user_id)` 取长期用药计划（药名/剂量/频次/服用时间）；
    3. **代码先拼一版结构化提醒**（药名 / 剂量 / 时间 / 是否漏服）——模型不可用时直接用它兜底；
    4. 用模型把提醒润色成对老人友好的话（更口语、更短句）；失败一律回退模板文案；
    5. 如果请求里出现"发邮件给家属/给张三发提醒"这类意图，语音提示可走 `medication_reminder` Skill，
       本节点只负责"提醒内容生成"，邮件发送属于工具层（MCP/本地工具）职责，不在此处写库或发信。

写入状态：medication_list、remind_content、task_results["medication"]、health_step="remind"。
"""
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.messages import AIMessage

from app.ai.agent.multi_agent.state.health_state import HealthState
from app.ai.model.my_model import MyModel, model_fallback_middleware
from app.ai.prompt.bulider_prompt import BuilderPromptYaml
from app.core.logger import logger
from app.dao.medication_dao import MedicationDAO

# 读取外部提示词（模块顶层加载）
prompt = BuilderPromptYaml.get_prompt("remind_node.yaml")

# 需要提示老人关注的状态
MISSED_STATUS = "漏服"
PENDING_STATUS = "待服"


def _extract_question(state: HealthState) -> str:
    """取本轮用户问题：最后一条 HumanMessage（兜底取最后一条消息内容）"""
    messages = state.get("messages") or []
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content or "")
    if messages:
        return str(getattr(messages[-1], "content", "") or "")
    return ""


def _template_remind(today: list[dict], plan: list[dict]) -> str:
    """
    代码模板文案：药名 / 剂量 / 时间 / 是否漏服（AC-05 要求"含药名、剂量、服用时间"）。
    这是模型润色失败时的兜底，也是模型润色的输入底稿。
    """
    lines: list[str] = []
    if today:
        lines.append("今天需要吃的药：")
        for record in today:
            drug = record.get("drug_name") or record.get("name") or "（未命名药物）"
            dosage = record.get("dosage") or ""
            plan_time = record.get("plan_time") or record.get("take_time") or ""
            status = record.get("status") or PENDING_STATUS
            mark = "（已服用）" if status == "已服" else (f"（{status}，请尽快补服并告知家属）" if status == MISSED_STATUS else f"（{status}）")
            lines.append(f"  · {drug} {dosage}　时间：{plan_time} {mark}")
    elif plan:
        lines.append("您的长期用药计划：")
        for item in plan:
            drug = item.get("drug_name") or "（未命名药物）"
            dosage = item.get("dosage") or ""
            frequency = item.get("frequency") or ""
            take_time = item.get("take_time") or ""
            lines.append(f"  · {drug} {dosage}　频次：{frequency}　时间：{take_time}")
        lines.append("今天还没有生成待服记录，记得按时间吃药。")
    else:
        return "目前没有查到您的用药计划。如果需要，我可以帮您记录，或者请家属帮您完善用药计划。"

    # 漏服单独强调（老人最容易忘的就是这一条）
    missed = [r for r in today if str(r.get("status")) == MISSED_STATUS]
    if missed:
        names = "、".join(str(r.get("drug_name") or "未命名药物") for r in missed)
        lines.append(f"注意：{names} 显示漏服，请补服并联系家属确认，切勿自行加倍剂量。")
    return "\n".join(lines)


async def _polish(template: str) -> str:
    """
    模型润色：把结构化提醒改成对老人友好的口语化短句。
    失败（模型不可用/超时）返回模板原文——提醒内容绝不因为模型问题而丢失。
    """
    try:
        agent = create_agent(model=MyModel.get_model(), middleware=model_fallback_middleware(), system_prompt=prompt)
        result = await agent.ainvoke({"messages": [HumanMessage(content=f"请把下面这份用药提醒改写成对老人说的话：\n{template}")]})
        content = ""
        if isinstance(result, dict):
            messages = result.get("messages") or []
            if messages:
                content = str(getattr(messages[-1], "content", "") or "")
        content = content.strip()
        if content:
            return content
        logger.warning("[Remind] 模型返回空内容，使用模板文案")
        return template
    except Exception as e:
        logger.error(f"[Remind] 提醒文案润色失败，使用模板文案：{e}")
        return template


def _maybe_email_hint(question: str, remind_content: str) -> str:
    """
    若用户明确要求"给家属发提醒邮件"，只做能力提示：
    真正的邮件发送属于工具层（MCP send_email / 本地 send_email_tool）与 medication_reminder Skill 的职责，
    本节点只负责提醒内容生成，不写库、不发信（保持节点职责单一）。
    """
    targets = ("家属", "子女", "儿子", "女儿", "老伴", "家人", "张三", "李四", "王五")
    if ("邮件" in question or "发给" in question) and any(word in question for word in targets):
        return remind_content + "\n（如需把这条提醒发给家属，请说「给家属发用药提醒邮件」，我会调用用药提醒技能来发送。）"
    return remind_content


async def remind_node(state: HealthState):
    """用药提醒节点：生成今日提醒文案"""
    user_id = str(state.get("user_id") or "")
    medication_list: list[dict] = []
    remind_content = ""
    try:
        logger.info(f"[Remind] 查询用户 {user_id} 的用药计划")
        # 同步 DAO：教学/演示规模直接在 async 节点调用；生产应改连接池/异步
        today = MedicationDAO.list_today(user_id) or []
        plan = MedicationDAO.list_plan(user_id) or []
        # medication_list 存"今日记录 + 长期计划"的合并视图，供前端展示
        medication_list = list(today) + [dict(item, _source="plan") for item in plan]
        logger.info(f"[Remind] 今日记录 {len(today)} 条，用药计划 {len(plan)} 条")

        # 1. 代码模板（含药名/剂量/时间/漏服）
        template = _template_remind(today, plan)
        # 2. 模型润色（失败回退模板）
        remind_content = await _polish(template)
        # 3. 若用户明确要求"给家属发邮件提醒"，只给出能力提示（真实发送由 Skill/工具层完成）
        question = _extract_question(state)
        remind_content = _maybe_email_hint(question, remind_content)

        return {
            "medication_list": medication_list,
            "remind_content": remind_content,
            "task_results": {
                "medication": {
                    "status": "ok",
                    "medication_list": medication_list,
                    "remind_content": remind_content,
                    "today_count": len(today),
                    "plan_count": len(plan),
                }
            },
            "health_step": "remind",
            "messages": [AIMessage(content=remind_content)],
        }
    except Exception as e:
        # 异常：写 error 让 Manager 重试；保留安全默认值
        logger.error(f"[Remind] 用药提醒生成失败：{e}")
        return {
            "medication_list": medication_list,
            "remind_content": remind_content,
            "task_results": {"medication": {"status": "error", "msg": str(e)}},
            "health_step": "remind",
            "messages": [AIMessage(content="用药信息暂时查不到，我稍后再帮您看一次。")],
        }


if __name__ == "__main__":
    # 自测说明：只验证纯函数（模板文案），不连数据库与模型。
    demo_today = [{"drug_name": "氨氯地平", "dosage": "5mg", "plan_time": "08:00", "status": "待服"}, {"drug_name": "二甲双胍", "dosage": "0.5g", "plan_time": "12:00", "status": "漏服"}]
    demo_plan = [{"drug_name": "阿司匹林", "dosage": "100mg", "frequency": "每日一次", "take_time": "20:00"}]
    print(_template_remind(demo_today, demo_plan))
    print("----- 无计划 -----")
    print(_template_remind([], []))
    # 真跑（需要 MySQL 数据）：
    # import asyncio
    # async def test():
    #     print(await remind_node({"user_id": "u001", "messages": []}))
    # asyncio.run(test())
