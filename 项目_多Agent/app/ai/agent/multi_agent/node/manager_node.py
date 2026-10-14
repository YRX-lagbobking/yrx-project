"""
主管节点（Supervisor）：整个多智能体图的"中枢"

职责（纯代码决策，不调用大模型）：
    1. 失败重试：当前任务结果 status=error 时重试，最多 3 次；超过次数降级到 chat 节点；
    2. 队列调度：从 task_queue 取出一个任务，用 Command(goto=...) 动态路由到对应 Worker；
    3. 二次校验：任务名不在白名单时降级到 chat 并记录未知任务日志（防 LLM 幻觉）；
    4. 收尾分流：队列为空时，多任务走 summarize 汇总，只有单个 chat 任务直接结束。

为什么用 Command(goto=...) 而不是条件边：
    条件边的跳转逻辑写在节点外部的映射函数里，编译期结构固定；
    Command 允许节点在运行时根据全局状态（队列、重试次数）动态决定去向，
    并且可以同时 update 状态（取走任务、递增重试计数），更适合本项目的集中式调度。
"""
from langgraph.graph import END
from langgraph.types import Command

from app.ai.agent.multi_agent.schema.plan_schema import TASK_ROUTES
from app.ai.agent.multi_agent.state.health_state import HealthState
from app.core.logger import logger

# 任务名 → Worker 节点名：**唯一数据源**是 schema/plan_schema.py 的 TASK_ROUTES，这里直接派生
# （不再手抄一份；"两份名单不同步"正是历史上 intent 节点名存实亡的根源）。
# 例：{"vitals":"data_check","knowledge":"rag","risk":"risk","medication":"remind",
#      "report":"report","notify":"notify","emotion":"chat","chat":"chat"}
ROUTE_MAP = dict(TASK_ROUTES)

# 单任务最大重试次数
MAX_RETRY = 3


def log_unknown_task(task_name: str, task: dict) -> None:
    """
    记录未知任务：如果某个未知任务频繁出现，说明 Planner 提示词需要调整，
    或者需要新增一个 Worker 节点。
    """
    logger.warning(
        f"未知任务：{task_name} | args={task.get('args')} | 已降级到 chat 节点"
    )


def _is_task_failed(task_results: dict, task_name: str) -> bool:
    """判断某个任务上一次执行是否失败"""
    if not task_name:
        return False
    result = task_results.get(task_name)
    if isinstance(result, dict) and result.get("status") == "error":
        return True
    return False


def manager_node(state: HealthState):
    """主管节点：根据任务队列动态调度下一个节点"""
    # 当前任务队列（复制一份，避免直接修改状态）
    queue = list(state.get("task_queue") or [])
    # 失败重试次数
    retry_count = state.get("retry_count", 0)
    # 当前任务
    current = state.get("current_task") or {}
    # 已有任务结果
    task_results = state.get("task_results") or {}
    # 取任务名
    current_name = current.get("task")

    logger.info(f"[Manager] 队列剩余={len(queue)} 当前任务={current_name} 重试={retry_count} 已完成={list(task_results.keys())}")

    # ---------- 1. 失败重试（超时/异常重试） ----------
    if _is_task_failed(task_results, current_name):
        if retry_count < MAX_RETRY:
            logger.warning(f"任务 {current_name} 执行失败，第 {retry_count + 1} 次重试")
            # 不把任务放回队列：它仍然是"当前任务"，直接重新路由到同一个 Worker
            return Command(
                update={"retry_count": retry_count + 1},
                goto=ROUTE_MAP.get(current_name, "chat"),
            )
        logger.error(f"任务 {current_name} 连续 {MAX_RETRY} 次失败，降级到 chat 节点")
        fallback = {
            "task": "chat",
            "args": {
                "content": (
                    f"系统处理「{current_name}」任务时多次失败。"
                    "请用温和友好的语气告诉用户：这部分暂时查不到，稍后再试试，"
                    "并询问他还有没有别的需要帮忙的。"
                )
            },
        }
        return Command(
            update={"task_queue": queue, "current_task": fallback, "retry_count": 0},
            goto="chat",
        )

    # ---------- 2. 队列已取空：本轮任务全部执行完 → 决定"结束"还是"汇总" ----------
    # ⚠️ 这里**不是**"Planner 没拆出任务"的兜底（planner 保证入口队列非空，见 _validate_tasks）；
    #    它是循环出口判断：queue 每调度一个任务就 pop 一个，取空即代表本轮跑完，
    #    所以每个任务执行后都会经过这里。
    if not queue:
        if not task_results:
            # 不可达防御：planner 必给任务、任何任务（含 error）都必写结果；
            # 走到这里只可能是状态被外部写坏（旧 checkpoint / 未来新增图入口），仅告警，
            # 并且和下面的正常汇总走同一个去向（不再重复 return）。
            logger.warning("队列已空且没有任何任务结果（状态异常），仍走 summarize 让用户拿到回复")
        elif len(task_results) == 1 and "chat" in task_results:
            # 优化而非兜底：chat 节点已经把答案流式推给前端了，再汇总会重复回答 + 多花一次模型调用
            logger.info("仅一个 chat 任务，直接结束")
            return Command(goto=END)
        logger.info("任务全部完成，进入 summarize 汇总")
        return Command(goto="summarize")

    # ---------- 3. 取出下一个任务并路由 ----------
    current = queue.pop(0) # ← 每调度一个任务就 pop 掉一个
    task_name = current.get("task")

    # 二次校验：防 LLM 幻觉拆出白名单外的任务
    if task_name not in ROUTE_MAP:
        log_unknown_task(task_name, current)
        current = {
            "task": "chat",
            "args": {
                "content": (
                    f"用户请求中有一部分我无法识别（原始任务标识：{task_name}）。"
                    "请先说明你理解的部分，再礼貌地询问用户具体想要什么。"
                )
            },
        }
        task_name = "chat"

    logger.info(f"[Manager] 调度任务 {task_name} → 节点 {ROUTE_MAP[task_name]}")
    return Command(
        update={"task_queue": queue, "current_task": current, "retry_count": 0},
        goto=ROUTE_MAP[task_name],
    )
