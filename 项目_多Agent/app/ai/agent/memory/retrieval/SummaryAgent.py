"""
四层记忆 · 抽取层（文件名首字母大写，沿用 exam_0914 的 SummaryAgent.py）

职责：
    把 L1 窗口记忆里的"最近对话"压缩成 L2 摘要记忆（300 字以内），
    让下一轮对话既能记住"这次聊过什么"，又不至于把历史原文全部塞进提示词。

摘要提示词的三条硬约束（医疗陪伴场景特有）：
    1. 保留老人的关键健康信息（症状、指标、慢病、用药）与已经给过的建议
       —— 否则会出现"上一轮刚说过，这一轮又推荐一遍"的糟糕体验；
    2. 去掉寒暄与情绪性废话 —— 摘要不参与共情，共情由当轮窗口记忆负责；
    3. 第三人称 + 300 字以内 + 只返回摘要 —— 便于直接拼进提示词，且不会随轮次膨胀。

降级设计：
    本地模型不可用 / 调用失败 → 规则摘要（直接截取最近 3 条对话）兜底，
    绝不抛异常、绝不让记忆层影响对话主流程。
"""
import asyncio

from langchain_core.messages import HumanMessage

from app.ai.agent.memory.save.summary_memory import SummaryMemory
from app.ai.model.my_model import MyModel
from app.core.logger import logger

# 摘要长度上限（字符数）
SUMMARY_MAX_CHARS = 300
# 规则兜底时取最近多少条对话
FALLBACK_RECENT_COUNT = 3


class SummaryAgent:
    """L2 摘要智能体：增量维护"旧摘要 + 最新记录 → 新摘要" """

    def __init__(self, summary_memory: SummaryMemory):
        # 摘要记忆的保存/查询对象（属性名与 exam_0914 保持一致）
        self.summary_memory = summary_memory
        # 摘要提示词
        self.prompt = self.get_prompt()
        # 模型与 agent 懒加载：构造期不建连，避免"记忆对象创建失败拖垮对话"
        self.model = None
        self.agent = None

    # ---------------- 提示词 ----------------
    def get_prompt(self) -> str:
        """摘要提示词：保留健康信息与已给建议，去掉寒暄，第三人称，300 字以内"""
        self.prompt = """
一、角色：
    你是独居老人健康管家系统的「会话摘要助手」，负责压缩老人与管家的多轮对话。

二、任务：
    根据「旧摘要」和「最新聊天记录」，生成一份新的会话摘要，用于下一轮对话的上下文。

三、必须保留的信息：
    1. 老人提到的关键健康信息：症状、身体感受、测量指标（血压/心率/血氧/血糖/体温/睡眠）；
    2. 老人的慢病史、过敏史、正在服用的药物、用药时间；
    3. 管家已经给过的建议（避免下一轮重复推荐同样的建议）；
    4. 老人明确提出的诉求与未解决的问题（例如"想让我儿子周末回来"）；
    5. 老人的情绪状态与沟通偏好（例如"说话要慢""听不清"）。

四、必须去掉的内容：
    1. 寒暄、客套、打招呼；
    2. 与健康无关的闲谈细节；
    3. 重复表述，同一信息只留一次。

五、格式要求：
    1. 使用第三人称（用"老人"指代，不写"您/我"）；
    2. 300 字以内，条目式陈述优先于长句；
    3. 只返回新摘要正文，不要标题、不要前后缀、不要解释、不要追问。

六、示例：
    输入（旧摘要：老人主诉头晕，已建议休息并复测血压。）
    最新记录：老人：我早上量了血压 150/95；管家：收缩压偏高，建议连测三天并联系家属。
    输出：老人主诉头晕并自测血压 150/95（收缩压偏高）；既往高血压，已建议连续三天固定时间测量、记录后联系家属；老人对"联系家属"态度犹豫。
"""
        return self.prompt

    # ---------------- 模型懒加载 ----------------
    def _get_agent(self):
        """懒加载摘要 agent；模型不可用时返回 None（上层走规则兜底）"""
        if self.agent is not None:
            return self.agent
        try:
            from langchain.agents import create_agent

            # 摘要属于轻量提炼任务，用本地模型即可（省成本、也不依赖外网）
            self.model = MyModel.get_local_model()
            self.agent = create_agent(
                model=self.model,
                system_prompt=self.prompt,
                tools=[],
                debug=False,
            )
        except Exception as e:
            logger.error(f"摘要模型不可用，将使用规则摘要兜底：{e}")
            self.agent = None
        return self.agent

    # ---------------- 记忆更新 ----------------
    async def update(self, messages: list) -> None:
        """把最新聊天记录合并进摘要记忆（失败不影响主流程）"""
        try:
            # 1. 查旧摘要（作为增量更新的输入）
            old_summary = await self.summary_memory.query()
        except Exception as e:
            logger.error(f"摘要记忆读取失败，按无旧摘要处理：{e}")
            old_summary = ""

        # 2. 把窗口消息拼成"最新聊天记录"文本
        records = self._format_messages(messages)
        question = f"旧摘要：{old_summary or '（暂无）'}\n最新聊天记录：\n{records}"

        # 3. 让模型生成新摘要；失败则用规则摘要兜底
        new_summary = ""
        agent = self._get_agent()
        if agent is not None:
            try:
                rs = await agent.ainvoke({"messages": [HumanMessage(content=question)]})
                new_summary = self._extract_content(rs)
            except Exception as e:
                logger.error(f"摘要模型调用失败，改用规则摘要：{e}")

        if not new_summary:
            new_summary = self._rule_summary(old_summary, messages)
            logger.warning("摘要记忆使用规则兜底（模型不可用）")

        # 4. 写入摘要记忆（超长时截断，保证提示词不膨胀）
        try:
            await self.summary_memory.save(new_summary[:SUMMARY_MAX_CHARS])
            logger.info(f"摘要记忆更新完成，长度={min(len(new_summary), SUMMARY_MAX_CHARS)}")
        except Exception as e:
            logger.error(f"摘要记忆写入失败：{e}")

    # ---------------- 内部工具 ----------------
    @staticmethod
    def _format_messages(messages: list) -> str:
        """把 [{"role","content"}] 拼成可读对话文本（非 dict 项自动跳过）"""
        lines = []
        for item in messages or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            # 角色中文映射：user → 老人，其余 → 管家
            speaker = "老人" if item.get("role") in ("user", "human") else "管家"
            lines.append(f"{speaker}：{content}")
        return "\n".join(lines) if lines else "（暂无）"

    @staticmethod
    def _extract_content(rs) -> str:
        """从 agent 返回值里取纯文本（兼容 dict / 消息对象两种返回形态）"""
        try:
            if isinstance(rs, dict):
                # create_agent 的标准返回：{"messages":[...]}
                msgs = rs.get("messages") or []
                if msgs:
                    return str(getattr(msgs[-1], "content", msgs[-1]) or "").strip()
                return str(rs.get("output", "") or "").strip()
            return str(getattr(rs, "content", rs) or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _rule_summary(old_summary: str, messages: list) -> str:
        """
        规则摘要兜底：不依赖任何模型。

        策略：旧摘要保留（说明这次会话之前聊过什么），再追加最近 3 条对话原文。
        """
        parts = []
        if old_summary and str(old_summary).strip():
            parts.append(str(old_summary).strip())
        recent = []
        for item in (messages or [])[-FALLBACK_RECENT_COUNT:]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            speaker = "老人" if item.get("role") in ("user", "human") else "管家"
            recent.append(f"{speaker}：{content}")
        if recent:
            parts.append("最近对话：" + "；".join(recent))
        return "；".join(parts)[:SUMMARY_MAX_CHARS] if parts else "（本轮无可摘要内容）"


if __name__ == "__main__":
    import asyncio

    async def test():
        # 自测：模型不可用时应当走规则摘要兜底，且不抛异常
        summary_memory = SummaryMemory("self_test_s001")
        agent = SummaryAgent(summary_memory)
        # 最近聊天记录
        record = [
            {"role": "user", "content": "你好，我叫张桂芬，今年 78 岁，有高血压"},
            {"role": "ai", "content": "张奶奶您好，我记住了，您有高血压"},
            {"role": "user", "content": "我早上量了血压 150/95，有点头晕"},
            {"role": "ai", "content": "收缩压偏高，建议连测三天并联系家属"},
        ]
        await agent.update(record)
        # 查询新摘要
        print("新摘要：", await summary_memory.query())

    asyncio.run(test())
