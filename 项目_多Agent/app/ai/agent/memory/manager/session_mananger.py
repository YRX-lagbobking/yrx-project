"""
记忆组装层 · 会话管理器（文件名沿用 exam_0914 的 session_mananger.py 拼写）

职责：
    一次对话所需的四层记忆对象 + 提示词组装器，全部在这里统一创建、统一对外，
    调用方（HealthGraphAgent）只需要：
        session_manager = SessionManager(session_id, user_id)
        await session_manager.save("user", question)
        prompt = await session_manager.build_prompt(user_id, question)

对外属性（【锁定】，调用方与记忆管理器都按名字取用）：
    window_memory / summary_memory / long_memory / profile_memory / prompt_builder

设计说明：
    1. 构造期不访问任何存储（只创建轻量对象），所以 SessionManager 一定构造成功；
    2. build_prompt 返回 **dict**（不是字符串），因为 LangGraph 的输入是
       [memory_prompt, HumanMessage(...)]，system 消息必须以 dict/BaseMessage 形式参与消息序列；
    3. 记忆任何一层失败都只降级，不影响对话主流程（NFR-02）。
"""
from app.ai.agent.memory.manager.prompt_builder import PromptBuilder
from app.ai.agent.memory.save.long_memory import LongMemory
from app.ai.agent.memory.save.profile_memory import ProfileMemory
from app.ai.agent.memory.save.summary_memory import SummaryMemory
from app.ai.agent.memory.save.window_memory import WindowMemory
from app.core.logger import logger


class SessionManager:
    """会话管理器：四层记忆对象的统一入口 + 系统提示词组装"""

    def __init__(self, session_id: str, user_id: str):
        # 会话编号（同时是 Checkpointer 的 thread_id）
        self.session_id = str(session_id)
        # 用户编号（老人 user_id）
        self.user_id = str(user_id)
        # L1 窗口记忆（按会话隔离）
        self.window_memory = WindowMemory(self.session_id)
        # L2 摘要记忆（按会话隔离）
        self.summary_memory = SummaryMemory(self.session_id)
        # L3 长期记忆（按用户隔离；内部自己完成 Milvus/Chroma/内存三级降级）
        self.long_memory = LongMemory()
        # L4 用户画像（按用户隔离）
        self.profile_memory = ProfileMemory(self.user_id)
        # 提示词组装器（四层记忆 → 中文 system 提示词）
        self.prompt_builder = PromptBuilder(self.session_id, self.user_id)

    # ---------------- 写入 ----------------
    async def save(self, role: str, content: str) -> None:
        """写一条对话进 L1 窗口记忆（role：user / ai）"""
        try:
            await self.window_memory.save(role, content)
        except Exception as e:
            # 窗口记忆失败不影响主流程（memory_manager 后续还会尝试更新 L2~L4）
            logger.error(f"窗口记忆写入失败：{e}")

    # ---------------- 组装 ----------------
    async def build_prompt(self, user_id: str, question: str) -> dict:
        """
        组装四层记忆提示词。

        返回值必须是 dict：{"role":"system","content":...}
        因为图输入是 [memory_prompt, HumanMessage(content=question)]。
        """
        # 以入参 user_id 为准（与构造参数一致，兼容调用方显式传参）
        target_user_id = str(user_id) if user_id else self.user_id
        try:
            content = await self.prompt_builder.builder_prompt(target_user_id, question)
        except Exception as e:
            # 记忆层失败不能让主流程挂掉：退化为"无记忆"的最小 system 提示词
            logger.error(f"四层记忆提示词构建失败，降级为无记忆模式：{e}")
            content = (
                "你是独居老人的智能健康管家，语气温和、耐心、口语化。"
                "输出不做医疗诊断，紧急情况引导拨打 120。"
            )
        return {"role": "system", "content": content}


if __name__ == "__main__":
    import asyncio

    async def test():
        # 自测：构造会话管理器 → 写两条窗口记忆 → 组装提示词（各层存储不可用时自动降级）
        manager = SessionManager("self_test_s001", "self_test_u001")
        await manager.save("user", "你好，我叫张桂芬，今年 78 岁，有高血压")
        await manager.save("ai", "张奶奶您好，我记住了，您有高血压，平时要按时吃药")
        prompt = await manager.build_prompt("self_test_u001", "我今天血压有点高怎么办")
        print("提示词类型：", type(prompt))
        print("提示词内容：")
        print(prompt.get("content", ""))

    asyncio.run(test())
