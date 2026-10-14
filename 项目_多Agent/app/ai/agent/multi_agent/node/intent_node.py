"""
Intent 节点：意图识别 + 情感标签（FR-05 / AC-07）

两道防线：
    1. 模型线：create_agent(response_format=IntentSchema) 输出 intent + emotion + confidence；
    2. 代码线：**纯关键词兜底**（不依赖任何外部服务），保证模型不可用/输出非法时仍能判对，
       这也是 AC-07（"我一个人在家有点害怕"→ anxious/lonely，回复先安抚后建议）的保障。

意图取值（IntentSchema）：health_query / medication / emergency / report / emotion / chat
情感取值（HealthState.emotion）：normal / anxious / low / happy / lonely

写入状态：intent、emotion、health_step="intent"，并追一条 AIMessage 说明识别结果。
"""
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.messages import AIMessage

from app.ai.agent.multi_agent.schema.intent_schema import IntentSchema
from app.ai.agent.multi_agent.state.health_state import HealthState
from app.ai.model.my_model import MyModel
from app.ai.prompt.bulider_prompt import BuilderPromptYaml
from app.core.logger import logger

# 读取外部提示词（模块顶层加载）
prompt = BuilderPromptYaml.get_prompt("intent_node.yaml")

# 意图白名单（与 IntentSchema 的取值一致）
INTENTS = ("health_query", "medication", "emergency", "report", "emotion", "chat")
# 情感白名单（与 HealthState 注释一致）
EMOTIONS = ("normal", "anxious", "low", "happy", "lonely")

# ---------------- 关键词兜底词表（按优先级从高到低判断） ----------------
# 紧急：最高优先级，宁可误报不可漏报
EMERGENCY_WORDS = (
    "急救", "120", "晕倒", "晕过去", "昏倒", "喘不上气", "喘不过气", "呼吸困难", "呼吸急促",
    "胸口疼", "胸痛", "胸闷", "心慌得厉害", "意识不清", "昏迷", "抽搐", "大出血", "摔倒起不来",
)
# 用药
MEDICATION_WORDS = ("药", "吃了没", "漏服", "服药", "服药记录", "按时吃", "药盒", "降压药", "忘记吃")
# 报告
REPORT_WORDS = ("报告", "周报", "日报", "月报", "总结一下最近", "健康报告", "体检报告")
# 情感（同时给出对应情感标签）
EMOTION_WORDS = {
    "anxious": ("害怕", "紧张", "焦虑", "担心", "慌", "睡不着觉", "心慌", "不安", "恐惧"),
    "lonely": ("孤单", "孤独", "想你", "没人陪", "没人说话", "一个人在家", "寂寞"),
    "low": ("难受", "没意思", "没劲", "郁闷", "提不起", "心里堵", "想哭", "不想说话", "低落", "烦"),
    "happy": ("高兴", "开心", "心里踏实", "挺好的", "真不错", "谢谢"),
}
# 体征 / 健康问询
HEALTH_WORDS = (
    "血压", "心率", "心跳", "血糖", "血氧", "体温", "睡眠", "身体", "不舒服", "头晕", "头疼", "头痛",
    "乏力", "没力气", "指标", "体征", "健康", "怎么样",
)


def _extract_question(state: HealthState) -> str:
    """取本轮用户问题：最后一条 HumanMessage（兜底取最后一条消息内容）"""
    messages = state.get("messages") or []
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content or "")
    if messages:
        return str(getattr(messages[-1], "content", "") or "")
    return ""


def keyword_intent(question: str) -> tuple[str, str]:
    """
    纯代码关键词兜底：返回 (intent, emotion)。

    优先级：emergency > medication > report > emotion > health_query > chat。
    说明：情感标签不是"负面才识别"，老人说"今天挺高兴"同样要判 happy（情感陪伴不只是安慰）。
    """
    text = question or ""
    # 1. 紧急
    if any(w in text for w in EMERGENCY_WORDS):
        return "emergency", "anxious"
    # 2. 用药
    if any(w in text for w in MEDICATION_WORDS):
        return "medication", "normal"
    # 3. 报告
    if any(w in text for w in REPORT_WORDS):
        return "report", "normal"
    # 4. 情感（命中情绪词即判 emotion，并给出对应标签）
    for emotion, words in EMOTION_WORDS.items():
        if any(w in text for w in words):
            return "emotion", emotion
    # 5. 体征问询
    if any(w in text for w in HEALTH_WORDS):
        return "health_query", "normal"
    # 6. 兜底聊天
    return "chat", "normal"


def _normalize(data: dict) -> tuple[str, str]:
    """把模型输出规范化为合法取值；非法时回退关键词结果"""
    intent = str(data.get("intent") or "").strip()
    emotion = str(data.get("emotion") or "").strip()
    if intent not in INTENTS:
        logger.warning(f"意图识别模型输出非法 intent={intent}，回退关键词判定")
        intent = ""
    if emotion not in EMOTIONS:
        logger.warning(f"意图识别模型输出非法 emotion={emotion}，回退关键词判定")
        emotion = ""
    return intent, emotion


async def intent_node(state: HealthState):
    """意图识别节点：输出 intent + emotion"""
    # 关键词兜底结果（模型失败时直接使用）
    question = _extract_question(state)
    fallback_intent, fallback_emotion = keyword_intent(question)
    try:
        logger.info(f"[Intent] 用户问题：{question}")
        # 1. 创建意图识别智能体（结构化输出）
        agent = create_agent(
            model=MyModel.get_model(),
            system_prompt=prompt,
            response_format=IntentSchema,
        )
        # 2. 异步调用
        user_msg = {"messages": [HumanMessage(content=question)]}
        result = await agent.ainvoke(user_msg)
        schema = result.get("structured_response")
        data = schema.model_dump() if schema is not None else {}
        intent, emotion = _normalize(data)

        # 3. 任何一个非法 → 用关键词兜底（两个字段一起兜，避免"模型 intent + 兜底 emotion"错配）
        if not intent or not emotion:
            intent, emotion = fallback_intent, fallback_emotion

        logger.info(f"[Intent] 识别结果 intent={intent} emotion={emotion} confidence={data.get('confidence')}")
        return {
            "intent": intent,
            "emotion": emotion,
            "health_step": "intent",
            "messages": [AIMessage(content=f"意图识别完成：intent={intent}，emotion={emotion}")],
        }
    except Exception as e:
        # 4. 异常兜底：纯代码关键词判定，保证流程继续（也是 AC-07 的保障）
        logger.error(f"[Intent] 意图识别失败，使用关键词兜底：{e}")
        return {
            "intent": fallback_intent,
            "emotion": fallback_emotion,
            "health_step": "intent",
            "messages": [AIMessage(content=f"意图识别降级为关键词判定：intent={fallback_intent}，emotion={fallback_emotion}")],
        }


if __name__ == "__main__":
    # 自测说明：下面只验证"纯代码关键词兜底"，不连模型、不连数据库。
    cases = [
        "我一个人在家有点害怕",
        "我胸口疼，喘不上气，快帮我打急救电话",
        "中午的药我吃了没？",
        "帮我生成一份这周的健康周报",
        "我最近血压怎么样",
        "今天天气不错，跟我说说话",
    ]
    for q in cases:
        print(f"{q}  ->  {keyword_intent(q)}")
    # 真跑模型（需要有效 DASHSCOPE_API_KEY）：
    # import asyncio
    # print(asyncio.run(intent_node({"messages": [HumanMessage(content="我一个人在家有点害怕")]})))
