"""
多智能体节点包（Supervisor-Worker 架构的 Worker 集合）

节点清单与职责（与《开发规范与接口契约》第 13 节路由表严格对应）：
    planner_node     复合意图拆解：LLM 拆任务 → 写 task_queue（白名单约束 + 结果校验）
    manager_node     主管调度：纯代码决策，Command(update/goto) 动态路由（**框架方提供，本包不改动**）
    intent_node      意图识别 + 情感标签（含纯代码关键词兜底，保障 AC-07）
    data_check_node  取最新生命体征 + 数据质检（缺失字段尝试补查）
    rag_node         健康知识检索（向量 → 关键词 → 静态提示 三级降级，结果带溯源）
    risk_node        三层风险检测与分级（等级以纯计算层代码判定为准，模型只做措辞润色）
    report_node      日报/周报生成（Skill 指令 + 四章节 + 流式输出 + 落盘 Markdown）
    remind_node      用药提醒生成（药名/剂量/时间/漏服，模型润色失败走模板兜底）
    notify_node      双通道分级通知（家属 SMS/邮件 + 老人端 WebSocket；高危支持人工确认）
    summarize_node   多任务结果按用户提问顺序汇总（流式输出）
    chat_node        普通聊天 / 情感陪伴 / 未知任务反问澄清（流式输出）

约定：
    1. 除 manager_node 外全部是 async def，签名统一为 `async def xxx_node(state: HealthState) -> dict`；
    2. 所有节点内部 try/except，失败写 `task_results[task] = {"status": "error", "msg": ...}`，
       Manager 会据此自动重试，超过 3 次降级 chat（见 node/manager_node.py）；
    3. 流式节点（chat / summarize / report）用 `langgraph.config.get_stream_writer()` 逐段推送。
"""

# 各节点函数（显式导入，便于 `from app.ai.agent.multi_agent.node import planner_node` 的方式调用）
NODE_EXPORTS = [
    "planner_node",
    "chat_node",
    "data_check_node",
    "intent_node",
    "notify_node",
    "rag_node",
    "remind_node",
    "report_node",
    "risk_node",
    "summarize_node",
]

__all__ = NODE_EXPORTS


if __name__ == "__main__":
    # 自测说明：不在 __init__ 里导入具体节点，避免"导入包即触发模型/数据库初始化"。
    # 真正要用某个节点时按全路径导入，例如：
    #     from app.ai.agent.multi_agent.node.planner_node import planner_node
    print("多智能体节点包，包含：", "、".join(NODE_EXPORTS))
