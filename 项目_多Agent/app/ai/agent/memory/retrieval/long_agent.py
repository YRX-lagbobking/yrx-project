"""
四层记忆 · 抽取层 · L3 长期记忆智能体

职责：
    从"老人本轮说的话 + 最近对话"里抽取**值得长期记住的稳定事实**，写入长期记忆库。

什么算"稳定事实"（跨会话依然成立）：
    1. 慢病史（高血压、糖尿病、冠心病……）；
    2. 过敏史（青霉素过敏、海鲜过敏……）；
    3. 长期用药（药名、剂量、服用时间）；
    4. 家庭情况（和谁住、子女是否在本地、紧急联系人）；
    5. 沟通偏好（说慢一点、用方言、听不清要大点声）。

什么不算：
    一次性的症状、当天的指标数值、寒暄、情绪波动 —— 这些属于 L1 窗口 / L2 摘要的范畴，
    写进长期记忆只会污染检索结果。

硬性约定：
    模型判断"没有值得记住的信息"时返回空字符串，**空字符串一律不写库**
    （否则长期记忆会被大量空记录占满，检索精度骤降）。
    模型不可用时不报错，直接跳过本轮写入。
"""
from langchain_core.messages import HumanMessage

from app.ai.agent.memory.save.long_memory import LongMemory
from app.ai.model.my_model import MyModel
from app.core.logger import logger

# 单条长期记忆的长度上限（Milvus VARCHAR 字段 2048，这里更保守，避免写超长噪声）
LONG_MEMORY_MAX_CHARS = 200


class LongAgent:
    """L3 长期记忆智能体：抽取稳定事实并写入长期记忆"""

    def __init__(self, long_memory: LongMemory):
        # 长期记忆的存储对象（属性名与 exam_0914 保持一致）
        self.long_memory = long_memory
        # 抽取提示词
        self.prompt = self.get_prompt()
        # 模型与 agent 懒加载：构造期不建连
        self.model = None
        self.agent = None

    # ---------------- 提示词 ----------------
    def get_prompt(self) -> str:
        """抽取提示词：只抽稳定事实，只输出事实本身，无信息返回空字符串"""
        self.prompt = """
一、角色：
    你是独居老人健康管家系统的「长期记忆抽取助手」。
    长期记忆用于跨会话记住老人的稳定情况，因此只记录"下周、下个月依然成立"的事实。

二、任务：
    从「老人说的话」和「最近对话」中，抽取值得长期记住的稳定事实。

三、只允许抽取以下五类（每类可多条，每条一句话）：
    1. 慢病史：如"老人有高血压病史 10 年"；
    2. 过敏史：如"老人对青霉素过敏"；
    3. 长期用药：如"老人每天早饭后服用氨氯地平 5mg"；
    4. 家庭情况：如"老人与老伴同住，儿子在外地工作，每周回来一次"；
    5. 沟通偏好：如"老人听力下降，希望说话慢一点、声音大一点"。

四、严禁记录：
    1. 一次性的症状或当天的测量数值（如"今天头晕""血压 150/95"）；
    2. 寒暄、客套、情绪波动、与健康无关的闲谈；
    3. 推测、编造、补全的信息（不确定的一律不写）；
    4. 管家的建议（那是对话内容，不是老人的稳定事实）。

五、输出要求：
    1. 每条事实独立成行，以"- "开头；
    2. 使用第三人称客观陈述（用"老人"指代）；
    3. 文字简洁，200 字以内；
    4. 只输出事实本身，不要标题、不要解释、不要追问；
    5. **如果没有任何值得长期记住的信息，直接返回空字符串**（不要输出"无"、不要输出"暂无可记录信息"）。

六、示例：
    输入：我今年 78 了，有高血压，对青霉素过敏，平时儿子不在身边，你说话慢点。
    输出：
    - 老人 78 岁，有高血压病史
    - 老人对青霉素过敏，用药前需确认
    - 老人独居，儿子不在身边
    - 老人希望说话语速放慢

    输入：我今天头有点晕，血压 150/95。
    输出：（空字符串）
"""
        return self.prompt

    # ---------------- 模型懒加载 ----------------
    def _get_agent(self):
        """懒加载长期记忆抽取 agent；模型不可用时返回 None（跳过本轮写入）"""
        if self.agent is not None:
            return self.agent
        try:
            from langchain.agents import create_agent

            # 抽取任务用本地模型即可
            self.model = MyModel.get_local_model()
            self.agent = create_agent(
                model=self.model,
                system_prompt=self.prompt,
                tools=[],
                debug=False,
            )
        except Exception as e:
            logger.error(f"长期记忆模型不可用，本轮跳过长期记忆写入：{e}")
            self.agent = None
        return self.agent

    # ---------------- 记忆更新 ----------------
    async def update(self, user_id: str, question: str) -> None:
        """抽取稳定事实并写入长期记忆；无信息或模型不可用时安静跳过"""
        # 输入为空：直接返回，不做无意义的模型调用
        text = "" if question is None else str(question).strip()
        if not text:
            return

        agent = self._get_agent()
        if agent is None:
            return

        # 提问 → 抽取
        try:
            rs = await agent.ainvoke({"messages": [HumanMessage(content=text)]})
            content = self._extract_content(rs)
        except Exception as e:
            logger.error(f"长期记忆抽取失败（跳过本轮写入）：{e}")
            return

        # 空结果不写库（硬性要求：避免空记录污染长期记忆）
        content = self._clean(content)
        if not content:
            logger.info("本轮没有可长期记住的信息，跳过长期记忆写入")
            return

        # 写入长期记忆（内部自动走 Milvus → ChromaDB → 内存字典三级降级）
        try:
            await self.long_memory.save(user_id, content[:LONG_MEMORY_MAX_CHARS])
            logger.info(f"长期记忆已更新：{content[:60]}...")
        except Exception as e:
            logger.error(f"长期记忆写入失败：{e}")

    # ---------------- 内部工具 ----------------
    @staticmethod
    def _extract_content(rs) -> str:
        """从 agent 返回值里取纯文本（兼容 dict / 消息对象两种返回形态）"""
        try:
            if isinstance(rs, dict):
                msgs = rs.get("messages") or []
                if msgs:
                    return str(getattr(msgs[-1], "content", msgs[-1]) or "").strip()
                return str(rs.get("output", "") or "").strip()
            return str(getattr(rs, "content", rs) or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _clean(content: str) -> str:
        """
        清洗模型输出：去掉"无/暂无"类占位文案，避免把空语义写进长期记忆。

        模型经常不听话地输出"（空字符串）""无""暂无可记录信息"这类文本，
        它们对新会话没有任何信息量，必须过滤掉。
        """
        text = "" if content is None else str(content).strip()
        if not text:
            return ""
        # 去掉包裹的引号与括号
        text = text.strip("\"'“”「」（）()【】[] ")
        # 占位话术黑名单（保守枚举，命中即视为空）
        placeholders = (
            "无", "暂无", "空", "空字符串", "没有", "无信息", "暂无可记录信息",
            "无可记录信息", "没有可记录", "none", "null", "n/a", "na",
        )
        if text.lower() in placeholders or len(text) <= 1:
            return ""
        # 逐行过滤占位行
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        lines = [line for line in lines if line.strip("-• *").strip().lower() not in placeholders]
        return "\n".join(lines)


if __name__ == "__main__":
    import asyncio

    async def test():
        # 自测：模型不可用时应当安静跳过（不抛异常、不写库）
        long_memory = LongMemory()
        agent = LongAgent(long_memory)
        # 含稳定事实的输入
        await agent.update("self_test_u001", "我今年 78 岁，有高血压，对青霉素过敏，儿子在外地，你说话慢点")
        # 只含一次性信息的输入（应当不写库）
        await agent.update("self_test_u001", "我今天头有点晕")
        print("长期记忆检索：", await long_memory.query("self_test_u001", "老人有什么病"))
        print("清洗占位文案：", repr(agent._clean("暂无可记录信息")))

    asyncio.run(test())
