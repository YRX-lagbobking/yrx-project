"""
健康管家多智能体 —— 全局状态

设计说明：
    1. messages 用 operator.add 累加（与 exam_0914 保持一致的写法），
       这样每个节点返回 {"messages":[AIMessage(...)]} 都会追加而不是覆盖；
    2. 任务队列方案：task_queue（待执行） + current_task（当前） + task_results（结果），
       让 Manager 能"取一个、执行一个、回主管、再取下一个"，支持复合意图；
       ⚠️ task_results 必须带**合并 reducer**（不能是裸 dict）：LangGraph 对无 reducer 的字段
          使用 LastValue 通道 = 每步整体覆盖，多任务时后一个任务的结果会把前一个覆盖掉，
          导致 summarize 只能看到最后一个任务（多任务汇总丢内容）。
          合并语义 {**old, **new}；每轮清空必须用 {"__reset__": True} 标记
          （在合并语义下直接给 {} 等于什么都没做）。
    3. 检测与风险字段全部落到状态里，便于 Summarize 节点统一汇总与前端展示；
    4. health_step 是主管节点路由的"状态机游标"，与 exam_state 中的 exam_step 同义。

⚠️ 文件结构约定：类定义必须是连续的（字段之间不要插入三引号字符串）。
   曾经有人把 task_results 的示例贴进类体中间，第 0 列的 ''' 会提前结束类定义，
   导致后面的字段落到类外 → IndentationError（应用直接起不来）。
   示例请统一放到文件末尾的"附录"区。
"""
import operator

from typing_extensions import Annotated, TypedDict

from langchain.messages import AnyMessage


def merge_task_results(old: dict | None, new: dict | None) -> dict:
    """
    task_results 的合并写入器（reducer）。

    为什么要 reducer：
        LangGraph 对"没有 reducer 的字段"使用 LastValue 通道 → 每步**整体覆盖**。
        各 Worker 只返回自己那一格（如 {"risk": {...}}），于是后执行的任务会把前面任务的结果
        整个覆盖掉，summarize 只能看到最后一个任务。
    每轮清空：
        合并语义下 `{}` 合并进旧值等于"没清空"，所以 planner/图入口用 {"__reset__": True}
        显式表示"新一轮开始，清空结果"（该标记不会留在状态里）。
    """
    old = old or {}
    new = new or {}
    if new.get("__reset__"):
        return {}
    return {**old, **new}


class HealthState(TypedDict):
    """健康管家图状态"""

    # ---- AI 消息列表（各节点过程记录，operator.add 累加） ----
    messages: Annotated[list[AnyMessage], operator.add]

    # ---- 会话与身份 ----
    # 老人/用户编号
    user_id: str
    # 会话编号（同时作为 Checkpointer 的 thread_id）
    session_id: str
    # 角色：elder 老人 / family 家属 / admin 管理员
    role: str

    # ---------- Planner 任务拆解 ----------
    # 任务队列 [{"task":"vitals","args":{}}]
    task_queue: list[dict]
    # 当前正在执行的任务
    current_task: dict
    # 各任务执行结果 {task_name: {...}}
    # ⚠️ 带合并 reducer：各节点只返回自己那一格，由 reducer 负责合并（详见 merge_task_results）
    task_results: Annotated[dict, merge_task_results]

    # ---------- 意图识别（已合并进 planner：PlanSchema 顶层字段） ----------
    # 意图：health_query / medication / emergency / report / emotion / chat
    intent: str
    # 情感标签：normal / anxious / low / happy / lonely
    emotion: str

    # ---------- 数据质检节点 ----------
    # 最新生命体征
    vitals: dict
    # 数据是否可用
    data_available: bool
    # 缺失的指标名
    data_missing: list[str]

    # ---------- RAG 知识检索节点 ----------
    # 知识片段 [{"content","source","version","page","score"}]
    knowledge: list[dict]

    # ---------- 风险判断节点 ----------
    # 风险等级：高危 / 中危 / 低危 / 正常
    risk_level: str
    # 风险原因说明
    risk_reasons: list[str]
    # 命中的异常项明细
    anomalies: list[dict]
    # 是否需要通知家属
    need_notify: bool

    # ---------- 报告生成节点 ----------
    # 报告类型：daily / weekly
    report_type: str
    # 报告正文
    report_content: str
    # 报告导出文件路径
    report_path: str

    # ---------- 用药提醒节点 ----------
    # 用药计划列表
    medication_list: list[dict]
    # 提醒文案
    remind_content: str

    # ---------- 家属通知节点 ----------
    # 实际使用的通知通道
    notify_channel: str
    # 通知结果
    notify_result: str

    # ---------- 记忆与技能 ----------
    # 四层记忆组装出的系统提示词
    memory_prompt: str
    # 本次命中的 Skill 名称
    skill_name: str

    # ---------- 流程控制 ----------
    # 当前流程状态：start/planned/data_check/rag/risk/report/remind/notify/done
    health_step: str
    # 已执行任务计数器
    current: int
    # 任务失败重试次数（超过上限降级到 chat）
    retry_count: int
    # 是否需要人工确认后才发通知（高危短信 + AUTO_NOTIFY_SMS=false）
    need_human_confirm: bool
    # 最终回复
    final_answer: str


# ===========================================================================
# 附录：task_results 的实际形状示例（纯文档，不参与运行）
# ---------------------------------------------------------------------------
# 位置要求：这类示例必须放在类定义**之外**（本文件末尾）；
#          贴在类体中间的第 0 列三引号会提前结束类定义 → IndentationError。
# ===========================================================================
TASK_RESULTS_EXAMPLES = """
# 单任务：普通聊天（chat_node.py:156）
{"chat": {"status": "ok", "answer": "我在的，您慢慢说…", "mode": "普通聊天", "emotion": "normal"}}

# 体征（data_check_node.py:117，注意 key 是 vitals 不是 data_check）
{"vitals": {"status": "ok",
            "vitals": {"heart_rate": 78, "blood_pressure_sys": 135, "spo2": 96},
            "missing": ["sleep_hours"], "data_available": True}}

# 知识检索（rag_node.py:143）
{"knowledge": {"status": "ok", "count": 3,
               "knowledge": [{"content": "老年人应低盐饮食…", "source": "高血压防治指南", "page": 2}]}}

# 风险（risk_node.py:128）
{"risk": {"status": "ok", "risk_level": "中危",
          "risk_reasons": ["收缩压 152 mmHg 高于参考上限"],
          "anomalies": [{"metric": "blood_pressure_sys", "level": "中危", "value": 152}],
          "need_notify": True}}

# 用药（remind_node.py:139）
{"medication": {"status": "ok",
                "medication_list": [{"drug_name": "苯磺酸氨氯地平片", "dosage": "5mg",
                                     "take_time": "08:00", "status": "待服", "_source": "plan"}],
                "remind_content": "爷爷，早上8点…", "today_count": 3, "plan_count": 3}}

# 报告（report_node.py:375）
{"report": {"status": "ok", "report_type": "weekly", "title": "本周健康报告",
            "report_path": "app/html/reports/xxx.md", "report_content": "# …"}}

# 通知（notify_node.py:202）
{"notify": {"status": "ok", "level": "中危", "notified": True, "channel": "websocket",
            "content": "家属您好，老人…", "result": "…", "need_human_confirm": True}}

# 失败时统一是"短形态"（各节点一致）
{"risk": {"status": "error", "msg": "…"}}

# 多任务合并后的样子（task_results 带合并 reducer，每个任务各占一个 key）
{"vitals": {"status": "ok", ...},
 "risk": {"status": "ok", "risk_level": "中危", ...},
 "medication": {"status": "ok", "remind_content": "爷爷，早上8点…", ...}}
"""
