"""
Planner 的结构化输出定义 + **任务路由的唯一数据源**（TASK_ROUTES）

设计变更（原 intent 节点已合并进 planner）：
    原来：planner 只输出 tasks；若要"意图 + 情感"，必须额外拆一个 intent 任务，
          再由 manager 调度独立 intent 节点**第二次调用模型** → 职责重叠、漏拆就丢情感标签。
    现在：planner 这一次调用同时输出 intent + emotion + tasks。

单一数据源（本次收敛）：
    任务名 → Worker 节点名 这份映射以前被写在 4 个地方：
        ① 本文件的 TaskItem.description  ② planner_node.yaml 提示词
        ③ planner_node.TASK_WHITELIST    ④ manager_node.ROUTE_MAP
    任何一处改漏都会出现"名单不同步"（intent 被合并时就踩过一次）。
    现在 TASK_ROUTES 是唯一权威：
        · planner_node.TASK_WHITELIST = tuple(TASK_ROUTES.keys())
        · manager_node.ROUTE_MAP      = dict(TASK_ROUTES)
        · TaskItem.description        = 由 TASK_ROUTES 的 key 生成
        · 提示词 yaml                 由 app/test/test_routes.py 断言"用到的任务名 ⊆ TASK_ROUTES"
"""
from pydantic import BaseModel, Field

# 任务名 → Worker 节点名（唯一数据源；节点名必须存在于图中，见 app/test/test_routes.py）
TASK_ROUTES: dict[str, str] = {
    "vitals": "data_check",      # 取最新生命体征 + 数据质检
    "knowledge": "rag",          # 健康知识检索
    "risk": "risk",              # 三层风险检测与分级
    "medication": "remind",      # 用药提醒
    "report": "report",          # 报告生成（日报/周报）
    "notify": "notify",          # 家属通知（双通道）
    "emotion": "chat",           # 情感陪伴（走 chat 节点）
    "chat": "chat",              # 普通聊天兜底
}

# 白名单（由 TASK_ROUTES 派生，保持向后兼容的模块级常量名）
TASK_WHITELIST: tuple[str, ...] = tuple(TASK_ROUTES.keys())

# 任务名列表文本（给模型的字段说明用，避免手写第二份名单）
_TASK_NAMES_TEXT = "、".join(TASK_ROUTES.keys())


class TaskItem(BaseModel):
    """单个子任务"""

    task: str = Field(
        ...,
        description=f"任务名，只能是 {_TASK_NAMES_TEXT} 中的一个",
    )
    args: dict = Field(
        default_factory=dict,
        description="任务参数，例如 report 任务的 {'type':'weekly'}、chat 任务的 {'content':'讲个笑话'}",
    )


class PlanSchema(BaseModel):
    """一次调用同时给出「意图 + 情感 + 任务队列」"""

    intent: str = Field(
        default="chat",
        description="用户意图，只能是 health_query、medication、emergency、report、emotion、chat 中的一个",
    )
    emotion: str = Field(
        default="normal",
        description="用户情感标签，只能是 normal、anxious、low、happy、lonely 中的一个",
    )
    tasks: list[TaskItem] = Field(
        ...,
        description="拆解后的有序任务列表，按用户表述顺序排列；单需求时只返回一个任务",
    )
