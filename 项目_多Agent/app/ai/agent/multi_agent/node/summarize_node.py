"""
Summarize 节点：多任务结果汇总（FR-10 / AC-16）—— 流式节点

职责：
    把 `task_results` 里的各任务结论**按用户提问顺序**整合成一段连贯、温和的回复。
    顺序来源：`task_queue` 里的任务名顺序（Planner 已按用户表述顺序排列），
    所以这里不会再"重新排序"，避免答非所问。

流式输出：`get_stream_writer()` 逐段 write；完整文本写 messages（AIMessage）与 final_answer。
降级：模型不可用或无输出时，用代码模板把各任务结论拼成一段话（保证"一定有回复"）。

写入状态：final_answer、messages、health_step="done"。
"""
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer

from app.ai.agent.multi_agent.state.health_state import HealthState
from app.ai.model.my_model import MyModel, model_fallback_middleware
from app.ai.prompt.bulider_prompt import BuilderPromptYaml
from app.core.logger import logger

# 读取外部提示词（模块顶层加载）
prompt = BuilderPromptYaml.get_prompt("summarize_node.yaml")

# 任务名 → 中文说明（给模型看，避免模型理解错任务含义）
TASK_CN = {
    "intent": "意图识别",
    "vitals": "最新体征数据",
    "knowledge": "健康知识检索",
    "risk": "风险检测与分级",
    "medication": "用药提醒",
    "report": "健康报告",
    "notify": "家属通知",
    "emotion": "情感陪伴",
    "chat": "一般对话",
}
# 每个任务结果传给模型时的最大字符数（防止上下文过长）
MAX_RESULT_CHARS = 800


def _extract_question(state: HealthState) -> str:
    """取本轮用户问题：最后一条 HumanMessage（兜底取最后一条消息内容）"""
    messages = state.get("messages") or []
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content or "")
    if messages:
        return str(getattr(messages[-1], "content", "") or "")
    return ""


def _ordered_tasks(state: HealthState) -> list[str]:
    """
    按用户提问顺序取"有结果的任务"：
    task_queue 里剩下的任务（未执行完）不展示；已执行的按队列顺序输出；
    再补上不在队列里的结果键（兜底，防止顺序信息缺失时漏内容）。
    """
    results = state.get("task_results") or {}
    order: list[str] = []
    for item in state.get("task_queue") or []:
        name = (item or {}).get("task") if isinstance(item, dict) else None
        if name and name in results and name not in order:
            order.append(name)
    for name in results.keys():
        if name not in order:
            order.append(name)
    return order


def _result_text(name: str, result) -> str:
    """把单个任务结果压成一段可读文本（截断超长内容，保留结论要点）"""
    if isinstance(result, dict):
        if result.get("status") == "error":
            return f"执行失败（{result.get('msg', '未知原因')}）"
        for key in ("final_answer", "remind_content", "report_content", "content", "answer", "summary"):
            if result.get(key):
                return str(result[key])
        parts = []
        for key, value in result.items():
            if key in ("status", "knowledge", "anomalies", "medication_list", "report_content"):
                continue
            parts.append(f"{key}={value}")
        return "；".join(parts) or "（无细节）"
    return str(result)


def _fallback_answer(state: HealthState, ordered: list[str]) -> str:
    """模型不可用时的模板汇总：把每个任务结论按顺序拼成一段温和的话"""
    results = state.get("task_results") or {}
    lines = ["我把刚才问到的情况给您梳理一下："]
    for name in ordered:
        lines.append(f"- {TASK_CN.get(name, name)}：{_result_text(name, results.get(name))}")
    risk_level = state.get("risk_level") or "正常"
    if risk_level in ("高危", "中危"):
        lines.append("这次指标有点偏离平时的范围，我已经提醒家属关注；如果您觉得不舒服，请马上联系家人或拨打 120。")
    else:
        lines.append("整体看没有明显问题，记得按时吃药、规律作息。有不舒服随时告诉我。")
    return "\n".join(lines)


async def summarize_node(state: HealthState):
    """汇总节点（流式）：按提问顺序整合成一段连贯回复"""
    ordered = _ordered_tasks(state)
    try:
        results = state.get("task_results") or {}
        question = _extract_question(state)
        logger.info(f"[Summarize] 汇总任务顺序={ordered}")

        # 1. 组装汇总输入（关键结论 + 风险等级 + 数据缺失情况）
        detail_lines = [f"- {TASK_CN.get(name, name)}：{_result_text(name, results.get(name))}" for name in ordered]
        user_content = "\n".join(
            [
                f"用户原始提问：{question}",
                f"风险等级：{state.get('risk_level') or '未检测'}",
                f"风险原因：{state.get('risk_reasons') or []}",
                f"数据缺失项：{state.get('data_missing') or []}",
                "各子任务结论（请按此顺序整合）：",
                "\n".join(detail_lines) or "（无子任务结论）",
            ]
        )

        # 2. 流式生成（片段实时推送 + 累加完整文本）
        writer = get_stream_writer()
        fragments: list[str] = []
        try:
            agent = create_agent(model=MyModel.get_model(), middleware=model_fallback_middleware(), system_prompt=prompt)
            async for chunk, meta in agent.astream(
                {"messages": [HumanMessage(content=user_content)]}, stream_mode="messages"
            ):
                piece = getattr(chunk, "content", None)
                if piece:
                    fragments.append(piece)
                    writer(piece)
        except Exception as e:
            logger.error(f"[Summarize] 模型汇总失败，使用模板兜底：{e}")

        final_answer = "".join(fragments).strip()
        # 3. 模型不可用 → 模板兜底（必须有回复，否则用户看到空白）
        if not final_answer:
            final_answer = _fallback_answer(state, ordered)
            writer(final_answer)

        return {
            "final_answer": final_answer,
            "messages": [AIMessage(content=final_answer)],
            "health_step": "done",
        }
    except Exception as e:
        # 异常兜底：仍然要给用户一段可读回复（汇总节点是链路最后一环，不能再往上抛）
        logger.error(f"[Summarize] 汇总节点异常：{e}")
        answer = _fallback_answer(state, ordered) or "抱歉，整理结果时出了点问题，请再说一遍您想了解什么。"
        return {
            "final_answer": answer,
            "messages": [AIMessage(content=answer)],
            "health_step": "done",
        }


if __name__ == "__main__":
    # 自测说明：只验证纯函数（顺序整理 / 结果压缩 / 模板兜底），不连模型。
    demo_state = {
        "messages": [],
        "task_queue": [],
        "task_results": {
            "vitals": {"status": "ok", "vitals": {"heart_rate": 78}, "missing": ["sleep_hours"]},
            "risk": {"status": "ok", "risk_level": "低危", "risk_reasons": ["舒张压略高"]},
        },
        "risk_level": "低危",
        "data_missing": ["sleep_hours"],
    }
    print("顺序：", _ordered_tasks(demo_state))
    print("模板兜底：")
    print(_fallback_answer(demo_state, _ordered_tasks(demo_state)))
    # 真跑（需要模型）：
    # import asyncio
    # print(asyncio.run(summarize_node(demo_state)))
