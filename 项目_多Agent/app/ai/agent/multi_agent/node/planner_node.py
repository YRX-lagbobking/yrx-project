"""
Planner 节点：复合意图任务拆解（Supervisor-Worker 的"入口")

职责（对应 FR-10 / 需求文档 5.10 与 7.2）：
    1. 用 LLM（create_agent + response_format=PlanSchema）把用户请求拆成**有序**任务列表；
    2. **结果校验（防幻觉）**：过滤掉白名单之外的任务名，与 manager_node.ROUTE_MAP 保持完全一致；
    3. 白名单过滤后为空 → 退化为 [{"task": "chat"}]；LLM 异常 → 同样退化为 chat，并把原问题
       放进 args.content，让 chat 节点"先说明理解的部分再反问澄清"（AC-18）；
    4. 单需求时队列长度就是 1（退化兼容），只有复合意图才拆多个任务。

白名单（8 个，必须与 manager_node.ROUTE_MAP 的键一致）：
    vitals / knowledge / risk / medication / report / notify / emotion / chat
    ⚠️ intent 已**合并进本节点**（方案 B）：不再作为独立任务与独立节点，
       由 PlanSchema 顶层字段 intent/emotion 直接给出 → 一次模型调用同时完成
       "意图/情感识别"与"任务拆解"，消除与旧 intent 节点的职责重叠。

写入状态：task_queue、intent、emotion、task_results（用 {"__reset__": True} 清空上一轮结果）、
         retry_count=0、health_step="planned"，并追一条 AIMessage。
"""
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.messages import AIMessage

from app.ai.agent.multi_agent.node.intent_node import EMOTIONS, INTENTS, keyword_intent
from app.ai.agent.multi_agent.schema.plan_schema import TASK_ROUTES, PlanSchema
from app.ai.agent.multi_agent.state.health_state import HealthState
from app.ai.model.my_model import MyModel, model_fallback_middleware
from app.ai.prompt.bulider_prompt import BuilderPromptYaml
from app.core.logger import logger

# 读取外部提示词（模块顶层加载，避免每次调用都读文件）
prompt = BuilderPromptYaml.get_prompt("planner_node.yaml")

# 任务白名单：**唯一数据源**是 schema/plan_schema.py 的 TASK_ROUTES（任务名 → Worker 节点名），
# 这里与 manager_node.ROUTE_MAP 都由它派生，不再各写一份（历史上两份名单不同步出过 bug）。
TASK_WHITELIST = tuple(TASK_ROUTES.keys())

# 紧急关键词：命中时自动补一个 notify 任务，保证"紧急情况一定推动通知"（安全兜底）
EMERGENCY_WORDS = ("急救", "120", "晕倒", "喘不上气", "呼吸困难", "呼吸急促", "胸口疼", "胸痛", "胸闷", "意识", "昏迷")
# 体征/体检类关键词：命中时补 vitals（"身体怎么样"这类复合意图需要先取数据）
VITALS_WORDS = ("身体", "血压", "心率", "血糖", "血氧", "体温", "睡眠", "体检", "指标")


def _extract_question(state: HealthState) -> str:
    """
    从消息历史里取出"本轮用户问题"。

    说明：记忆层注入的 system 提示词与历史对话都在 messages 中，
    这里只取最后一条 HumanMessage 作为本轮问题（兜底取最后一条消息内容）。
    """
    messages = state.get("messages") or []
    for msg in reversed(messages):
        #从最后一条往前遍历找到humanMessage
        if isinstance(msg, HumanMessage):
            return str(msg.content or "")
    # 如果循环走完了都没找到 HumanMessage，就取最后一条消息的内容(兜底)
    if messages:
        return str(getattr(messages[-1], "content", "") or "")
    return ""

def _item_to_dict(item) -> dict | None:
    """
    把模型返回的单个任务项规范化为 {"task": str, "args": dict}。

    模型可能返回 TaskItem 对象、dict、甚至 None/字符串，这里统一处理并做白名单过滤。
    """
    if item is None:
        return None
    # TaskItem / dict 两种形态
    if isinstance(item, dict):
        raw_task = item.get("task")
        raw_args = item.get("args")
    else:
        # TaskItem 对象取其属性
        raw_task = getattr(item, "task", None)
        raw_args = getattr(item, "args", None)
    task = str(raw_task or "").strip()
    # "intent" 已合并进本节点（意图/情感由顶层字段给出）→ 静默丢弃，不当成"白名单外的幻觉任务"
    if task == "intent":
        logger.info("Planner 输出的 intent 任务已合并进意图字段，自动忽略")
        return None
    if task not in TASK_WHITELIST:
        logger.warning(f"Planner 拆出白名单外的任务，已过滤：{task}")
        return None
    # args 必须是 dict，其它类型（str/None/list）统一兜成 {} 或 {"content": ...}
    if isinstance(raw_args, dict):
        args = dict(raw_args)
    elif raw_args:
        args = {"content": str(raw_args)}
    else:
        args = {}
    return {"task": task, "args": args}


def _validate_tasks(plan: PlanSchema | None, question: str) -> list[dict]:
    """
    结果校验：白名单过滤 + 去重 + 退化兜底。

    - 过滤白名单外的任务（AC-17 的第三层防御，Manager 里还有一次二次校验）；
    - 保序去重，避免重复任务把队列撑大；
    - 过滤后为空 → 退化为 [{"task": "chat"}]（原问题放进 args.content）。
    """
    tasks: list[dict] = []
    seen: set[str] = set()
    raw_tasks = getattr(plan, "tasks", None) or []
    for item in raw_tasks:
        task = _item_to_dict(item)
        if not task or task["task"] in seen:#不在白名单内或者已经出现过
            continue
        seen.add(task["task"])
        tasks.append(task)

    # 一个任务都没有（模型返回空 / 全部被过滤）→ 退化为 chat
    if not tasks:
        logger.warning("Planner 拆解结果为空，退化为 chat 任务")
        return [{"task": "chat", "args": {"content": question}}]

    names = {t["task"] for t in tasks}

    # 安全兜底 1：紧急词汇 → 必须带 notify（"喘不上气/晕倒/急救"这类请求不能被拆丢通知环节）
    if any(word in question for word in EMERGENCY_WORDS) and "notify" not in names:
        logger.warning("Planner 检测到紧急表述，自动补充 notify 任务")
        tasks.append({"task": "notify", "args": {}})

    # 安全兜底 2：问"身体/血压"等但没取体征 → 自动补 vitals（保证 risk/report 有数据可算）
    if any(word in question for word in VITALS_WORDS) and "vitals" not in names:
        if {"risk", "report"} & names:
            logger.info("Planner 检测到体征类复合意图，自动补充 vitals 任务")
            tasks.insert(0, {"task": "vitals", "args": {}})

    return tasks


def _plan_intent_emotion(plan: PlanSchema | None, question: str) -> tuple[str, str]:
    """
    取"意图 + 情感"（方案 B：与任务拆解共用同一次模型调用）。

    校验规则与旧 intent 节点保持一致：
        · intent 必须属于 INTENTS、emotion 必须属于 EMOTIONS；
        · 任一非法/缺失 → **两个字段一起**回退纯代码 keyword_intent
          （避免"模型 intent + 兜底 emotion"这种错配）；
    这样即使模型漏填，emotion 也不会丢 —— chat_node 的情感陪伴模式依赖它（AC-07）。
    """
    data = plan.model_dump() if plan is not None else {}
    intent = str(data.get("intent") or "").strip()
    emotion = str(data.get("emotion") or "").strip()
    if intent in INTENTS and emotion in EMOTIONS:
        return intent, emotion
    fb_intent, fb_emotion = keyword_intent(question)
    logger.warning(
        f"Planner 意图/情感字段非法或缺失（intent={intent!r} emotion={emotion!r}），"
        f"回退关键词判定：intent={fb_intent} emotion={fb_emotion}"
    )
    return fb_intent, fb_emotion


async def planner_node(state: HealthState):
    """Planner 节点：一次调用同时产出「意图/情感」与「有序任务队列」"""
    # 已执行任务计数器从 0 开始
    # task_results 带"合并 reducer"（见 health_state.merge_task_results），
    # 所以每轮清空必须用 {"__reset__": True}：直接给 {} 在合并语义下等于"什么都没做"，
    # 上一轮的结果会残留并被 summarize 一起汇总进去。
    base_update = {"task_results": {"__reset__": True}, "retry_count": 0, "current": 0, "health_step": "planned"}
    try:
        # 1. 取本轮用户问题
        question = _extract_question(state)
        logger.info(f"[Planner] 收到问题：{question}")

        # 2. 创建任务拆解智能体（结构化输出 PlanSchema）
        agent = create_agent(
            model=MyModel.get_model(),
            middleware=model_fallback_middleware(),
            system_prompt=prompt,
            response_format=PlanSchema,
        )
        # 3. 异步调用
        user_msg = {"messages": [HumanMessage(content=question)]}
        result = await agent.ainvoke(user_msg)
        plan = result.get("structured_response")
        logger.info(f"[Planner] 模型原始拆解结果：{plan}")

        # 4. 结果校验（白名单过滤 + 退化兜底）
        task_queue = _validate_tasks(plan, question)

        # 5. 意图 + 情感（与任务拆解同一次模型调用的产物，替代原独立 intent 节点）
        intent, emotion = _plan_intent_emotion(plan, question)

        # 6. 写状态 + 说明消息
        update = dict(base_update)
        update["task_queue"] = task_queue
        update["intent"] = intent
        update["emotion"] = emotion
        update["messages"] = [AIMessage(content=f"已规划 {len(task_queue)} 个任务：{[t['task'] for t in task_queue]}")]
        logger.info(f"[Planner] 意图={intent} 情感={emotion}")
        logger.info(f"[Planner] 最终任务队列：{task_queue}")
        return update
    except Exception as e:
        # 6. 异常兜底：退化为单个 chat 任务，并把原问题交给 chat 节点（AC-18 反问澄清）
        logger.error(f"[Planner] 任务拆解失败，退化为 chat：{e}")
        question = _extract_question(state)
        task_queue = [{"task": "chat", "args": {"content": question}}]
        # 模型不可用时，意图/情感走纯代码兜底（保证 chat_node 的情感陪伴模式仍有依据）
        intent, emotion = keyword_intent(question)
        update = dict(base_update)
        update["task_queue"] = task_queue
        update["intent"] = intent
        update["emotion"] = emotion
        update["messages"] = [AIMessage(content="任务拆解失败，已退化为闲聊兜底处理。")]
        logger.info(f"[Planner] 降级意图={intent} 情感={emotion}")
        return update


if __name__ == "__main__":
    # # 自测说明：这里只演示"结果校验"这一纯函数逻辑，不真的调用大模型（避免消耗额度/依赖网络）。
    # # 想真跑通请确保 .env 里的 DASHSCOPE_API_KEY 有效，再取消下面注释。
    # print("Planner 提示词前 120 字：", prompt[:120].replace("\n", " "))
    # print("白名单：", TASK_WHITELIST)
    # # 用假数据验证校验逻辑：混入白名单外的任务与重复任务
    # fake_plan = PlanSchema(intent="health_query", emotion="normal",
    #                        tasks=[{"task": "vitals", "args": {}}, {"task": "weather_query", "args": {}}, {"task": "vitals", "args": {}}])
    # print("校验结果：", _validate_tasks(fake_plan, "我身体怎么样"))
    # print("意图/情感（模型给的值）：", _plan_intent_emotion(fake_plan, "我身体怎么样"))
    # print("意图/情感（模型乱填 → 关键词兜底）：",
    #       _plan_intent_emotion(PlanSchema(intent="乱填", emotion="乱填", tasks=[{"task": "chat", "args": {}}]), "我一个人在家有点害怕"))
    # print("intent 任务被静默丢弃：",
    #       _validate_tasks(PlanSchema(tasks=[{"task": "intent", "args": {}}, {"task": "emotion", "args": {}}]), "我害怕"))
    # # 复合意图：应自动补 vitals 并保留 notify
    # print("紧急表述校验：", _validate_tasks(PlanSchema(tasks=[{"task": "risk", "args": {}}]), "我胸闷喘不上气"))
    async def test():
        state = {"messages": [HumanMessage(content="我最近血压怎么样？顺便提醒我吃药")]}
        print(await planner_node(state))
    import asyncio
    asyncio.run(test())
