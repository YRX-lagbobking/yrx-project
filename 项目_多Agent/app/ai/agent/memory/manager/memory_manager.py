"""
记忆组装层 · 记忆更新流水线（MemoryManager）

职责：
    一轮对话结束（已写入 user 提问 + ai 回复）之后，把这一轮"沉淀"成可复用的记忆：
        L1 窗口记忆：由 SessionManager.save 负责（本类只读不写）
        L2 摘要记忆：窗口消息 ≥ 2 条时由 SummaryAgent 增量更新
        L3 长期记忆：由 LongAgent 抽取稳定事实
        L4 用户画像：由 ProfileAgent 结构化抽取字段

执行顺序（有讲究）：
    先取窗口记忆 → 拼出"最近对话"文本 → 再并行更新 L3/L4 → 最后更新 L2 摘要。
    原因：L3/L4 只关心"用户说的话"，越早落库越好；L2 摘要需要完整的一轮问答，
    放在最后能拿到刚写进去的 ai 回复。

容错约定（硬性）：
    每一步都 try/except，失败只记日志、绝不向上抛异常；
    记忆更新失败最多导致"下一轮少一点上下文"，但绝不能让老人这一轮收不到回复。
"""
import asyncio

from app.ai.agent.memory.manager.session_mananger import SessionManager
from app.ai.agent.memory.retrieval.SummaryAgent import SummaryAgent
from app.ai.agent.memory.retrieval.long_agent import LongAgent
from app.ai.agent.memory.retrieval.profile_agent import ProfileAgent
from app.core.logger import logger

# 窗口消息达到该条数才触发摘要更新（1 问 1 答正好 2 条，即"至少完成一轮对话"）
SUMMARY_TRIGGER_COUNT = 2


class MemoryManager:
    """记忆更新管理器：一轮对话结束后的 L2/L3/L4 更新流水线"""

    def __init__(self, session_manager: SessionManager):
        # 复用会话管理器里已经建好的记忆对象，避免重复创建连接
        self.window_memory = session_manager.window_memory
        # 摘要智能体（L2）
        self.summary_agent = SummaryAgent(session_manager.summary_memory)
        # 长期记忆智能体（L3）
        self.long_agent = LongAgent(session_manager.long_memory)
        # 用户画像智能体（L4）
        self.profile_agent = ProfileAgent(session_manager.profile_memory)

    async def update(self, user_id: str, question: str) -> None:
        """更新 L2/L3/L4：每一步失败只记日志"""
        # ---------------- 1. 取窗口记忆 ----------------
        query_window = []
        try:
            query_window = await self.window_memory.query() or []
        except Exception as e:
            logger.error(f"记忆更新：窗口记忆查询失败（本轮跳过记忆沉淀）：{e}")
            return

        # ---------------- 2. 拼最近对话文本 ----------------
        # L3/L4 的输入是"老人说过的话"（AI 的话只作为上下文辅助，不喂给抽取智能体）
        try:
            lines = []
            user_lines = []
            for item in query_window:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content", "") or "").strip()
                if not content:
                    continue
                if item.get("role") in ("user", "human"):
                    lines.append(f"老人：{content}")
                    user_lines.append(content)
                else:
                    lines.append(f"管家：{content}")
            # 长期记忆：把本轮问题（最相关）与最近对话一起给出，便于抽取稳定事实
            long_input = question or ""
            if user_lines:
                context = "；".join(user_lines[-5:])
                long_input = f"{question}\n（老人最近说过：{context}）" if question else context
            # 画像抽取：只需要老人自己的原话，避免把管家的推测写进画像（画像必须"只提取明确信息"）
            profile_input = "；".join(user_lines[-5:]) if user_lines else (question or "")
            logger.info(f"记忆更新：窗口记忆 {len(query_window)} 条，对话文本 {len(lines)} 行")
        except Exception as e:
            logger.error(f"记忆更新：对话文本拼装失败（跳过本轮）：{e}")
            return

        # ---------------- 3. 并行更新 L3 长期记忆 / L4 用户画像 ----------------
        await asyncio.gather(
            self._safe_update(self.long_agent.update(user_id, long_input), "长期记忆(L3)"),
            self._safe_update(self.profile_agent.update(profile_input), "用户画像(L4)"),
        )

        # ---------------- 4. 更新 L2 摘要记忆 ----------------
        try:
            if len(query_window) >= SUMMARY_TRIGGER_COUNT:
                logger.info("记忆更新：开始更新摘要记忆(L2)")
                await self.summary_agent.update(query_window)
            else:
                logger.info("记忆更新：窗口消息不足一轮，跳过摘要更新")
        except Exception as e:
            logger.error(f"记忆更新：摘要记忆(L2)更新失败：{e}")

    # ---------------- 内部：单步容错 ----------------
    @staticmethod
    async def _safe_update(coro, name: str) -> None:
        """执行单个记忆更新协程，失败只记日志（不影响其它层与主流程）"""
        try:
            await coro
        except Exception as e:
            logger.error(f"记忆更新失败（{name}）：{e}")


if __name__ == "__main__":
    import asyncio

    async def test():
        # 自测：走一遍完整流水线（模型/存储不可用时应当只打日志、不抛异常）
        manager_session = SessionManager("self_test_s001", "self_test_u001")
        await manager_session.save("user", "我叫张桂芬，今年 78 岁，有高血压，对青霉素过敏")
        await manager_session.save("ai", "张奶奶您好，我记住了：高血压、青霉素过敏，用药前记得提醒医生")
        memory_manager = MemoryManager(manager_session)
        await memory_manager.update("self_test_u001", "我叫张桂芬，今年 78 岁，有高血压，对青霉素过敏")
        print("记忆更新流水线执行完成（失败只记日志，不影响主流程）")

    asyncio.run(test())
