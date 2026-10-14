"""
Chat 节点：兜底聊天 + 情感陪伴 + 未知任务澄清 + 家属留言（FR-05 / FR-10）—— 流式节点

四种模式（chat_node.yaml 里对每种模式都写了规则与话术）：
    ① 普通聊天；② 情感陪伴（emotion 命中 anxious/low/lonely）；③ 未知任务澄清；
    ④ **家属留言（方案 A：本节点挂工具）**：老人说"给儿子发个邮件"时，用工具真正发出去。

🛡️ 方案 A 的三条硬规则（都写在代码里，不依赖模型自觉）：
    1. **收件人白名单**：只允许发给"家属档案里的邮箱 + EMAIL_TO 兜底地址"，
       模型自己编的邮箱会被工具直接拒绝（老人不会背邮箱，模型只会编）；
    2. **确认闸门**：第一次调用必须 confirmed=false → 工具只返回草稿、不发送；
       老人明确说"发吧/可以/确认"后才用 confirmed=true 真正发送；
    3. **不外显**：工具返回值（内部草稿/参数）不推给用户 —— 见 _text_of()，否则老人会在
       屏幕上看到内部内容。

多轮上下文：为让"上一轮出草稿 → 这一轮说发吧"成立，本节点把最近 4 条"用户可见消息"
（过滤掉四层记忆提示词 dict 与"已规划 N 个任务"这类内部留痕）一起喂给内部 agent。

流式输出：get_stream_writer() 逐段 write；完整文本写 messages（AIMessage）与 final_answer。
写入状态：final_answer、messages（AIMessage）、task_results["chat"]、health_step="done"。
"""
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

from app.ai.agent.multi_agent.state.health_state import HealthState
from app.ai.model.my_model import MyModel, model_fallback_middleware
from app.ai.prompt.bulider_prompt import BuilderPromptYaml
from app.ai.tool.skill_tool import load_skill_tool
from app.skills.skill_loader import list_skills
from app.core.logger import logger

# 读取外部提示词（模块顶层加载）
prompt = BuilderPromptYaml.get_prompt("chat_node.yaml")

# 需要切换情感陪伴模式的情感标签
EMOTION_MODES = ("anxious", "low", "lonely")

# 情感陪伴模式的附加指令（与 chat_node.yaml 的规则保持一致）
EMOTION_INSTRUCTION = (
    "【情感陪伴模式】用户当前情绪低落或不安，请："
    "1) 先用一两句温和的话共情安抚（不否定、不说教、不夸张）；"
    "2) 再给一个马上能做到的小建议（如坐下慢慢呼吸、喝口温水、量一次血压）；"
    "3) 最后温和地建议联系家属或社区医生；"
    "4) 严禁给出任何诊断性结论（例如「你这是焦虑症」），也不要说「没事」「别想太多」这类敷衍话。"
)
# 未知任务澄清模式的附加指令（AC-18）
CLARIFY_INSTRUCTION = (
    "【澄清模式】系统没有完全识别用户这次请求的某一部分。请："
    "1) 先用一句话说明你理解到的部分（可以基于用户原话概括）；"
    "2) 再礼貌地反问澄清，用老人能听懂的话给出 1~2 个可选方向；"
    "3) 不要假装已经完成无法识别的操作，也不要编造数据。"
)
# 家属留言的附加指令（方案 A）
MAIL_INSTRUCTION = (
    "【家属留言规则】你可以用工具 send_family_mail 给家人发邮件：\n"
    "1) **只要老人提出「给儿子/女儿/家人发个邮件、捎句话、告诉家里人」，你的第一步动作就是"
    "调用 send_family_mail（confirmed=false）**，不要只用文字写一段草稿就算完；\n"
    "2) 收件人只能从下面【家属联系方式】里选 —— 这些邮箱来自 elder_family 家属绑定表，"
    "就是老人子女的收件邮箱（每行标注了「儿子/女儿」等关系，请按老人说的关系选人），不能编造邮箱；"
    "content 用老人要带的话；\n"
    "3) 工具会返回一份【草稿】，你把「发给谁 + 说什么」念给老人听，问一句"
    "「您看这样可以吗？您说发，我就发出去」；\n"
    "4) 老人明确同意（如「发吧」「可以」「确认」）后，再用 confirmed=true 调用一次真正发送；\n"
    "5) 只有工具返回「发送成功」才可以说已经发出；工具返回失败要如实告诉老人，不要说已经发送；\n"
    "6) 不要把家属的邮箱/手机号完整念给老人（老人也不需要记住这些）。"
)

# 多轮上下文取多少条"用户可见消息"（够支撑"草稿→确认→发送"即可）
HISTORY_LIMIT = 4


def _extract_question(state: HealthState) -> str:
    """取本轮用户问题：最后一条 HumanMessage（兜底取最后一条消息内容）"""
    messages = state.get("messages") or []
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content or "")
    if messages:
        return str(getattr(messages[-1], "content", "") or "")
    return ""


def _family_contacts(user_id: str) -> tuple[str, set[str]]:
    """
    查老人的**子女邮箱**（数据源：`elder_family` 家属绑定表）。

    为什么用 elder_family 而不是 user 表：
        `elder_family` 是"老人—家属"绑定表，自带 `relation`（儿子/女儿）与 `email`
        （老人为这位子女登记的收件邮箱），是"给老人的子女发邮件"最准确的数据源。

    返回 (给提示词用的文本, 允许发送的邮箱白名单)：
        · 白名单**只包含 elder_family 里的邮箱**（含 relation，模型按老人说的关系选人）；
        · 若 elder_family 里没有任何邮箱，才用 settings.email_to 兜底，并在文本中标注"系统兜底"。
    """
    allowed: set[str] = set()
    lines: list[str] = []
    try:
        from app.dao.user_dao import UserDAO

        family = UserDAO.list_family_contacts(user_id) or []
        for member in family:
            relation = str(member.get("relation") or "家属").strip()
            name = str(member.get("user_name") or "").strip()
            email = str(member.get("email") or "").strip()
            phone = str(member.get("phone") or "").strip()
            if not email:
                continue
            who = f"{relation}{f'（{name}）' if name else ''}"
            lines.append(f"- {who}：邮箱：{email}；手机：{phone or '（无）'}")
            allowed.add(email)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[Chat] 查询 elder_family 子女邮箱失败：{e}")

    if not allowed:                     # 兜底：elder_family 里没有可用邮箱
        try:
            from app.core.config import settings

            fallback = str(settings.email_to or "").strip()
            if fallback:
                allowed.add(fallback)
                lines.append(f"- （系统兜底邮箱；该老人在 elder_family 未登记子女邮箱）：{fallback}")
        except Exception:  # noqa: BLE001
            pass

    text = "\n".join(lines) if lines else "（该老人尚未在 elder_family 登记子女邮箱）"
    return (text, allowed)


def _recent_messages(state: HealthState, limit: int = HISTORY_LIMIT) -> list:
    """取最近几条"用户可见消息"（过滤四层记忆 dict 与内部状态留痕）"""
    out: list = []
    for msg in state.get("messages") or []:
        if isinstance(msg, HumanMessage):
            text = str(msg.content or "").strip()
            if text:
                out.append(HumanMessage(content=text))
        elif isinstance(msg, AIMessage):
            text = str(msg.content or "").strip()
            if text and not text.startswith(("已规划", "任务拆解", "意图识别")):
                out.append(AIMessage(content=text))
    return out[-limit:]


class FamilyMailParams(BaseModel):
    """send_family_mail 的入参"""

    to: str = Field(description="收件人邮箱，只能从【家属联系方式】里选，禁止编造")
    content: str = Field(description="邮件正文：老人要带给家人的话")
    subject: str = Field(default="", description="邮件主题，可不填")
    confirmed: bool = Field(
        default=False,
        description="老人是否已明确同意发送；第一次必须 false（只出草稿），同意后才可 true",
    )


def _make_mail_tool(allowed: set[str], calls: list):
    """构造家属留言工具（收件人白名单 + 确认闸门，两条规则都在代码里强制）"""

    @tool("send_family_mail", args_schema=FamilyMailParams)
    def send_family_mail(to: str = "", content: str = "", subject: str = "", confirmed: bool = False) -> str:
        """
        给家人发邮件。老人要求「给儿子/女儿/家人发邮件、捎句话、告诉家里人」时调用。
        收件人只能从系统提供的【家属联系方式】里选；第一次必须 confirmed=false（只出草稿），
        老人明确同意后才能用 confirmed=true 真正发送。
        """
        address = str(to or "").strip()
        body = str(content or "").strip()

        def emit(kind: str, text: str) -> str:
            calls.append({"kind": kind, "confirmed": bool(confirmed), "to": address})
            return text

        if not body:
            return emit("empty", "发送失败：正文是空的。请先问清楚老人想带给家人什么话。")
        if not address or address not in allowed:
            return emit(
                "rejected",
                "发送失败：这个邮箱不在家属名单里，不能发送。请只使用【家属联系方式】里的邮箱；"
                "如果没有可用的邮箱，就告诉老人暂时联系不上家人，请他稍后再试。",
            )
        if not confirmed:
            return emit(
                "draft",
                f"【草稿·尚未发送】收件人：{address}；主题：{subject or '来自智能健康管家的家人留言'}；"
                f"正文：{body}\n👉 请把收件人和正文念给老人听并请他确认；老人明确同意后，"
                f"再用 confirmed=true 调用一次真正发送。在他同意之前，不要说「已经发了」。",
            )

        from app.ai.tool.send_email_tool import send_email

        result = send_email(address, subject or "来自智能健康管家的家人留言", body)
        if isinstance(result, str) and "成功" in result:
            return emit("sent", f"发送成功：邮件已发送给 {address}。请如实告诉老人已发出。")
        return emit(
            "failed",
            f"发送失败：{result}。请如实告诉老人暂时没发出去，不要说已经发送，并说明稍后会再试。",
        )

    return send_family_mail


def _text_of(chunk) -> str:
    """取"可以给用户看"的模型输出文本；工具消息与工具参数一律不外显"""
    if isinstance(chunk, ToolMessage):
        return ""
    content = getattr(chunk, "content", None)
    if not isinstance(content, str) or not content.strip():
        return ""
    if getattr(chunk, "tool_call_chunks", None) and content.strip().startswith(("{", "[")):
        return ""
    return content


def _clarify_fallback(question: str) -> str:
    """未知任务/多次失败时的代码兜底话术（保证 AC-18：回复里一定有反问澄清）"""
    brief = (question or "").strip()
    if len(brief) > 40:
        brief = brief[:40] + "……"
    if brief:
        return (
            f"您说的「{brief}」，我大概听懂了您有这方面的需要，但这一句我还没完全确认清楚。\n"
            "您是想了解最近的身体指标，还是想让我帮您记一记吃药、或者生成一份健康报告呢？"
            "您再说一句，我马上帮您办。"
        )
    return (
        "刚才那段我没听清楚，怕帮您办错事。\n"
        "您是想了解身体指标、提醒吃药，还是生成健康报告？您再说一句，我马上帮您办。"
    )


def _emotion_fallback(emotion: str) -> str:
    """情感陪伴兜底话术：先安抚、再小建议、最后建议联系家属（无诊断性结论）"""
    return (
        "我在呢，您愿意跟我说说，我就在这儿陪着您。\n"
        "咱们先慢慢吸一口气、再慢慢呼出来，坐下来喝口温水；如果方便，顺手量一下血压和心跳。\n"
        "心里不踏实的时候，给家里人打个电话说说话会好受些；要是觉得胸闷、头晕得厉害，请马上联系家属或拨打 120。"
    )


def _chat_fallback(question: str) -> str:
    """普通聊天兜底话术"""
    return (
        "我在的，您慢慢说。\n"
        "我可以陪您聊聊天，也能帮您看最近的血压、心率这些指标，提醒您吃药，或者生成一份健康报告，"
        "还能帮您给家里人捎句话。您想从哪一件开始？"
    )


async def chat_node(state: HealthState):
    """聊天节点（流式 + 家属留言工具）：普通聊天 / 情感陪伴 / 未知任务澄清"""
    user_id = str(state.get("user_id") or "")
    question = _extract_question(state)
    emotion = str(state.get("emotion") or "normal")
    current = state.get("current_task") or {}
    args = current.get("args") or {}
    # 未知任务兜底指令（由 Manager 或 Planner 写入 args.content）
    unknown_content = str(args.get("content") or "").strip() if current.get("task") == "chat" else ""
    # 是否为澄清模式：
    # ① args.content 与用户原话不同（说明是系统写入的"未识别/失败"说明）；
    # ② 用户原话为空（没有可回答的内容）
    clarify_mode = bool(unknown_content and unknown_content != question) or not question
    try:
        # 1. 动态拼系统提示词：基础提示词 + 各模式附加指令（⚠️ 用累加，不能互相覆盖）
        mode = "普通聊天"
        extras: list[str] = []
        if emotion in EMOTION_MODES:
            extras.append(EMOTION_INSTRUCTION)
            mode = f"情感陪伴（{emotion}）"
        if clarify_mode:
            extras.append(CLARIFY_INSTRUCTION)
            mode = f"{mode}+澄清"
        contact_text, allowed_emails = _family_contacts(user_id)
        extras.append(f"【家属联系方式】\n{contact_text}")
        extras.append(MAIL_INSTRUCTION)
        # 技能目录（渐进式披露：只给 name+description，命中后再用 load_skill_tool 取正文）
        try:
            skills = list_skills()
        except Exception as e:  # 技能目录读不到不影响聊天
            skills = []
            logger.warning(f"[Chat] 技能目录加载失败（忽略）：{e}")
        if skills:
            catalog = "\n".join(f"- {s['name']}：{s['description']}" for s in skills)
            extras.append(
                "【可用技能】当用户请求属于下列固定套路时（如用药提醒邮件、日报、周报、情感安慰），"
                "先调用 load_skill_tool 取该技能的操作说明，再严格按说明执行：\n" + catalog
            )
        system_prompt = "\n\n".join([prompt, *extras])
        logger.info(f"[Chat] mode={mode} user_id={user_id} emotion={emotion} question={question}")

        # 2. 组装输入：最近几轮用户可见对话 + 本轮输入（支撑"草稿→确认→发送"跨轮生效）
        user_text = question
        if clarify_mode and unknown_content:
            user_text = f"用户原话：{question or '（空）'}\n系统说明：{unknown_content}"
        history = _recent_messages(state)
        if history and isinstance(history[-1], HumanMessage) \
                and str(history[-1].content).strip() == question.strip():
            history = history[:-1]                        # 本轮问题随后补，避免重复
        inputs = [*history, HumanMessage(content=user_text or "（用户没有说话）")]

        # 3. 挂工具 + 流式生成
        writer = get_stream_writer()
        fragments: list[str] = []
        calls: list[dict] = []                            # 工具调用记录（审计用）
        mail_tool = _make_mail_tool(allowed_emails, calls)
        try:
            # 模型降级：主模型调用失败 → 备用模型 → 本地模型（中间件在 create_agent 内部生效，
            # 因此 tools / 提示词 / 流式输出都不受影响）
            agent = create_agent(
                model=MyModel.get_model(),
                tools=[mail_tool, load_skill_tool],   # 家属留言工具 + 技能加载工具
                system_prompt=system_prompt,
                middleware=model_fallback_middleware(),
            )
            async for chunk, meta in agent.astream({"messages": inputs}, stream_mode="messages"):
                piece = _text_of(chunk)                   # ⚠️ 工具消息在此被过滤，不外显
                if piece:
                    fragments.append(piece)
                    writer(piece)
        except Exception as e:
            logger.error(f"[Chat] 模型/工具调用失败，使用兜底话术：{e}")

        answer = "".join(fragments).strip()
        # 4. 模型不可用 → 按模式给兜底话术（永远不返回空白）
        if not answer:
            if clarify_mode:
                answer = _clarify_fallback(question)
            elif emotion in EMOTION_MODES:
                answer = _emotion_fallback(emotion)
            else:
                answer = _chat_fallback(question)
            writer(answer)

        return {
            "final_answer": answer,
            "messages": [AIMessage(content=answer)],
            # 结果键固定 "chat"：Manager 依赖 "chat" in task_results 判断"仅闲聊可直接结束"
            "task_results": {"chat": {
                "status": "ok", "answer": answer, "mode": mode, "emotion": emotion,
                "tool_calls": calls,                      # 工具调用留痕（草稿/发送/拒绝）
            }},
            "health_step": "done",
        }
    except Exception as e:
        # 异常兜底：写 error 让 Manager 能感知，同时把可读话术返回给用户
        logger.error(f"[Chat] 聊天节点异常：{e}")
        answer = _clarify_fallback(question) if clarify_mode else _chat_fallback(question)
        return {
            "final_answer": answer,
            "messages": [AIMessage(content=answer)],
            "task_results": {"chat": {"status": "error", "msg": str(e), "answer": answer}},
            "health_step": "done",
        }


if __name__ == "__main__":
    # 自测说明：只验证纯函数（兜底话术、脱敏、流式过滤、历史过滤），不连模型、不发邮件。
    from langchain_core.messages import AIMessageChunk

    print("澄清兜底：", _clarify_fallback("帮我看看身体怎么样，顺便订个机票"))
    print("情感兜底：", _emotion_fallback("lonely"))
    print("闲聊兜底：", _chat_fallback("你好"))
    print("过滤 ToolMessage：",
          repr(_text_of(ToolMessage(content="【草稿·尚未发送】内部内容", tool_call_id="self_test"))))
    # 真实场景里的工具参数块带 tool_call_chunks 标记 → 必须被过滤掉
    args_chunk = AIMessageChunk(
        content='{"to": "a@b.com"}',
        tool_call_chunks=[{"name": "send_family_mail", "args": '{"to": "a@b.com"}',
                           "id": "call_1", "index": 0, "type": "tool_call_chunk"}],
    )
    print("过滤工具参数块：", repr(_text_of(args_chunk)))
    print("正常文本：", repr(_text_of(AIMessageChunk(content="我在呢，您慢慢说"))))
    print("提示词前 120 字：", prompt[:120].replace("\n", " "))
    # 真跑（需要模型 + 数据库）：
    # import asyncio
    # st = {"messages": [HumanMessage(content="我想给我儿子发个邮件，说我这周血压有点高")],
    #       "user_id": "u001", "emotion": "normal", "current_task": {"task": "chat", "args": {}}}
    # print(asyncio.run(chat_node(st)))
