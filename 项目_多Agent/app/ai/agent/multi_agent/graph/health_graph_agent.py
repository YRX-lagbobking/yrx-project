"""
健康管家多智能体（生产版：有并发场景）

相比 HealthGraph 多了四件事：
    1. 会话归属校验：Redis 里的 session:<uuid> 必须存在且 user_id 匹配，防伪造会话；
    2. 分布式会话锁：同一 session 同一时刻只允许一个请求进入（SET NX + Lua 原子释放）；
    3. 四层记忆：执行前用 PromptBuilder 组装 system 提示词，执行后更新 L1~L4；
    4. 技能直调：chat_skill() 支持指定 Skill（如 medication_reminder）直接执行。

Checkpointer 说明：
    开发态可用 InMemorySaver（重启即丢），生产态用 AsyncPostgresSaver（跨服务重启恢复记忆）。
    两者都通过构造函数注入，实现"会话状态与图执行"的解耦（换存储不改业务代码）。
"""
import contextlib
import json
import uuid
import time

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.ai.agent.memory.manager.memory_manager import MemoryManager
from app.ai.agent.memory.manager.session_mananger import SessionManager
from app.ai.agent.multi_agent.node.chat_node import chat_node
from app.ai.agent.multi_agent.node.data_check_node import data_check_node
from app.ai.agent.multi_agent.node.manager_node import manager_node
from app.ai.agent.multi_agent.node.notify_node import notify_node
from app.ai.agent.multi_agent.node.planner_node import planner_node
from app.ai.agent.multi_agent.node.rag_node import rag_node
from app.ai.agent.multi_agent.node.remind_node import remind_node
from app.ai.agent.multi_agent.node.report_node import report_node
from app.ai.agent.multi_agent.node.risk_node import risk_node
from app.ai.agent.multi_agent.node.summarize_node import summarize_node
from app.ai.agent.multi_agent.state.health_state import HealthState
from app.core.config import settings
from app.core.logger import logger
from app.dao import session_dao

# 内部状态消息前缀：这些 AIMessage 会写进 messages 做"过程留痕"，但不是给用户看的内容；
# 历史记录接口（load_history）按前缀过滤，避免"已规划 N 个任务"这类字样出现在界面上。
INTERNAL_PREFIXES = (
    "已规划", "任务拆解", "意图识别", "体征数据", "检索到", "风险检测",
    "通知处理完成", "答案已",
)


def _is_internal_note(content: str) -> bool:
    """判断一条 AI 消息是否为"内部状态留痕"（不外显给用户）"""
    text = (content or "").strip()
    return any(text.startswith(prefix) for prefix in INTERNAL_PREFIXES)


async def _save_history_message(session_id: str, user_id: str, role: str, content: str) -> None:
    """
    把"用户可见对话"写入 PostgreSQL 历史消息表（前端历史记录用）。

    与 L1 窗口记忆的分工：
        · Redis 窗口记忆：给**模型**看的最近 N 条上下文（短、会过期）；
        · chat_message 表：给**用户**看的历史（长期、可分页）。
    只写 user / bot 两类内容，所以历史里天然没有 planner / notify 等节点的内部留痕。
    session_dao 内部已有降级（PG 不可用只记日志），因此这里不再包 try/except。
    """
    text = str(content or "").strip()
    if text:
        await session_dao.append_message(str(session_id), str(role), text, str(user_id))


class HealthGraphAgent:
    """健康管家多智能体（有并发版）"""

    def __init__(self, checkpoint, redis):
        self.memory = checkpoint
        # 获取 redis 实例（异步客户端）
        self.redis = redis
        self.agent = self.get_agent()

    # 构建图
    def get_agent(self):
        graph = StateGraph(HealthState)

        # 添加节点
        graph.add_node("planner", planner_node)
        graph.add_node("manager", manager_node)
        graph.add_node("data_check", data_check_node)
        graph.add_node("rag", rag_node)
        graph.add_node("risk", risk_node)
        graph.add_node("report", report_node)
        graph.add_node("remind", remind_node)
        graph.add_node("notify", notify_node)
        graph.add_node("summarize", summarize_node)
        graph.add_node("chat", chat_node)

        # 画边
        graph.add_edge(START, "planner")
        graph.add_edge("planner", "manager")
        graph.add_edge("data_check", "manager")
        graph.add_edge("rag", "manager")
        graph.add_edge("risk", "manager")
        graph.add_edge("report", "manager")
        graph.add_edge("remind", "manager")
        graph.add_edge("notify", "manager")
        graph.add_edge("chat", "manager")
        graph.add_edge("summarize", END)

        # 编译
        self.agent = graph.compile(checkpointer=self.memory)
        return self.agent

    # ---------------- 会话管理 ----------------
    async def create_session(self, user_id) -> str:
        """创建会话：生成唯一 session_id 并记录归属，防止越权复用"""
        logger.info("创建会话")
        # 生成一个键名，采用 uuid，保持唯一性
        key = f"session:{uuid.uuid4().hex}"
        # 兜底：判断 redis 是否可用
        if self.redis:
            # 定义要存的数据，存入 user_id 和创建时间
            data = {"user_id": str(user_id), "create_time": time.time()}
            # 存入，过期时间是 1 天
            await self.redis.set(key, json.dumps(data, ensure_ascii=False), ex=60 * 60 * 24)
        # 同步写入 PostgreSQL 会话索引：历史记录列表的持久化数据源（Redis 过期后仍可恢复）
        await session_dao.create_session(key, str(user_id))
        return key

    async def check_session(self, user_id, session_id):
        """
        检查会话：会话 id 是否创建过 + 会话是否归属该用户
        返回 (是否通过, 原因)
        """
        logger.info(f"检查会话,会话id:{session_id}")
        if not session_id:
            return False, "会话id为空"
        # 兜底：Redis 不可用时不做归属校验（降级为可用）
        if not self.redis:
            logger.warning("Redis 不可用，跳过会话归属校验（降级）")
            return True, "Redis 不可用，已降级"

        # 获取会话数据
        data = await self.redis.get(session_id)
        logger.info(f"是否有会话:{data}")
        # 检查会话是否是该用户创建的或者是伪造的
        if not data:
            # Redis 过期（TTL 1 天）→ 回退查 PostgreSQL 会话索引，避免"隔天回来历史会话全没了"
            row = await session_dao.get_session(session_id)
            if row and str(row.get("user_id")) == str(user_id):
                logger.info("Redis 会话已过期，PostgreSQL 会话索引命中：已续期")
                try:
                    await self.redis.set(
                        session_id,
                        json.dumps({"user_id": str(user_id),
                                    "create_time": row.get("create_time") or time.time()},
                                   ensure_ascii=False),
                        ex=60 * 60 * 24,
                    )
                except Exception as e:
                    logger.warning(f"会话续期失败（不影响校验）：{e}")
                return True, "会话已续期（Redis 过期，PostgreSQL 索引命中）"
            logger.warning("会话不存在")
            return False, "会话不存在"
        # 检查会话归属
        rs = json.loads(data)
        if str(rs.get("user_id")) != str(user_id):
            logger.warning("会话不属于该用户")
            return False, "会话不属于该用户"
        logger.info("会话存在且归属该用户")
        return True, "会话存在且归属该用户"

    # ---------------- 历史记录（供前端列表与回溯） ----------------
    async def load_history(self, session_id: str) -> list:
        """
        从 Checkpointer 读回某会话的消息历史，返回 [{"role","content"}]。

        数据源：LangGraph 的 messages 通道（thread_id = session_id），含用户提问与各节点的 AI 消息；
        过滤：内部状态留痕（如 planner 的"已规划 N 个任务"）与空消息不返回；
        合并：同一轮里只保留最后一条 AI 消息（节点过程留痕不再各占一个气泡）。
        容错：任何异常都返回 []，绝不因历史读取失败影响对话（NFR-02）。
        """
        try:
            state = await self.agent.aget_state({"configurable": {"thread_id": session_id}})
            raw = (getattr(state, "values", None) or {}).get("messages", []) if state else []
        except Exception as e:
            logger.error(f"读取会话历史失败：{e}")
            return []

        history: list = []
        for msg in raw:
            content = str(getattr(msg, "content", "") or "").strip()
            if not content or _is_internal_note(content):
                continue
            role = "user" if getattr(msg, "type", "") == "human" else "bot"
            # 同一轮里只保留最后一条 AI 消息：planner / notify / remind 等节点的"过程留痕"
            # 会和最终回答一起出现在 messages 里，直接渲染就会变成多个气泡（历史"三段"问题）。
            # 一轮的最后一个节点必然是 chat / summarize（真正对用户说话的节点），取最后一条即可。
            if role == "bot" and history and history[-1]["role"] == "bot":
                history[-1] = {"role": "bot", "content": content}
                continue
            history.append({"role": role, "content": content})
        return history

    # ---------------- 对话 ----------------
    async def chat(self, question, user_id, session_id, role: str = "elder"):
        """对话主入口：SSE 流式产出文本片段"""
        logger.info(f"进入聊天:用户问题：{question},用户ID:{user_id},会话ID:{session_id}")

        # 1. 校验会话存在 + 归属匹配
        ok, reason = await self.check_session(user_id, session_id)
        logger.info(f"会话检查结果:{ok},{reason}")
        # 如果会话不存在或不属于该用户，直接返回原因
        if not ok:
            yield reason
            return

        # ----------------------- 添加记忆 -------------------------
        # 创建会话管理器
        session_manager = SessionManager(session_id, user_id)
        # 添加窗口记忆（用户提问）
        await session_manager.save("user", question)
        # 历史记录（前端列表用）：只写"用户可见对话"，与 L1 窗口记忆职责分开
        await _save_history_message(session_id, user_id, "user", question)
        # 构建四层记忆的提示词
        try:
            memory_prompt = await session_manager.build_prompt(user_id, question)
        except Exception as e:
            # 记忆层失败不能让主流程挂掉（降级）
            logger.error(f"记忆提示词构建失败，降级为无记忆模式：{e}")
            memory_prompt = {"role": "system", "content": "你是独居老人的智能健康管家。"}

        # 记录 AI 答案（用于写入窗口记忆）
        ai_answer = ""
        # --------------------------------------------------------
        # 构建用户问题 + 初始状态
        user_msg = {
            "messages": [memory_prompt, HumanMessage(content=question)],
            "user_id": str(user_id),
            "session_id": session_id,
            "role": role,
            "task_queue": [],
            # task_results 带合并 reducer：清空必须用 __reset__ 标记（合并语义下给 {} 不清空）；
            # planner 每轮也会再发一次同样的标记，双保险。
            "task_results": {"__reset__": True},
            "retry_count": 0,
            "health_step": "start",
        }
        # 配置检测点（thread_id 做会话隔离）
        config = {"configurable": {"thread_id": session_id}}

        try:
            async with self._session_lock(session_id):
                logger.info(f"开始处理会话:{session_id}")
                # 只开 custom 通道：由节点用 get_stream_writer() 决定"推给用户的内容"。
                # ⚠️ 绝对不要再加 "messages" 通道（踩过的坑，已用实验复现）——它会推送两类内容，
                #    导致前端"回答重复两遍 + 混入内部状态"：
                #      ① 每个节点 return 的 AIMessage（内部状态，如 planner 的"已规划 N 个任务"）；
                #      ② 节点内 LLM 的 token（chat_node 内部 agent.astream(stream_mode="messages") 会冒泡上来）。
                #    而 chat_node/summarize_node/report_node 又用 writer() 推了一遍同样的文本 → 重复。
                #    messages 通道只用于"状态与回溯"，不是用户可见内容。
                async for chunk in self.agent.astream(user_msg, config, stream_mode="custom"):
                    text = chunk if isinstance(chunk, str) else str(chunk)
                    if text:
                        ai_answer += text
                        yield text
            logger.info(f"AI回复:{ai_answer}")
            # 会话索引：刷新活跃时间 + 首次提问补标题（历史列表用；内部已降级，失败不影响对话）
            await session_dao.touch_session(session_id)
            await session_dao.set_title_if_default(session_id, session_dao.make_title(question))
        except Exception as e:
            logger.error(f"对话异常:{e}")
            yield str(e)

        # --------------------- 更新记忆 ---------------------------
        try:
            # 添加 AI 回复的记忆
            await session_manager.save("ai", ai_answer)
            # 历史记录（前端列表用）：只写用户实际看到的那段回答
            await _save_history_message(session_id, user_id, "bot", ai_answer)
            # 更新记忆（L2 摘要 / L3 长期 / L4 画像）
            memory_manager = MemoryManager(session_manager)
            await memory_manager.update(user_id, question)
        except Exception as e:
            logger.error(f"记忆更新失败（不影响回复）：{e}")
        # --------------------------------------------------------

    async def chat_skill(self, skill_name, question, user_id, session_id, role="elder"):
        """
        指定 Skill 直接执行（例如"帮我给张三发一封用药提醒邮件"）

        思路：把 SKILL.md 的完整指令作为前缀拼进用户输入，再走同一张图。
        这样"技能指令"和"业务编排"解耦：新增技能不需要改图。
        """
        from app.skills.skill_loader import load_skill

        try:
            instruction = load_skill(skill_name, question)
            logger.info(f"加载技能 {skill_name}，指令长度={len(instruction)}")
        except Exception as e:
            logger.error(f"技能 {skill_name} 加载失败：{e}")
            instruction = ""

        enriched = f"{instruction}\n\n用户原始请求：{question}" if instruction else question
        async for chunk in self.chat(enriched, user_id, session_id, role):
            yield chunk

    # ---------------- 会话锁 ----------------
    @contextlib.asynccontextmanager
    async def _session_lock(self, session_id: str):
        """
        会话锁：同一 session 同一时刻只允许一个请求进入。
        SET NX 抢锁，Lua 脚本原子释放，避免误删别人的锁。
        """
        if not self.redis:
            # Redis 不可用时不加锁（降级）
            yield
            return

        key = f"lock:session:{session_id}"
        # 生成随机 token
        token = uuid.uuid4().hex
        # 尝试获取锁，px 表示锁的过期时间（毫秒）
        # nx=True：只有 key 不存在时才设置成功
        ok = await self.redis.set(key, token, nx=True, px=5 * 60 * 1000)
        # 获取锁失败，说明会话正在处理中
        if not ok:
            raise RuntimeError("会话忙，请稍后再试")

        try:
            # yield 的位置就是"锁内要执行的代码"
            yield
        finally:
            # 兜底处理，确保锁一定能释放（使用 Lua 保证原子性，防止误释放）
            lua = """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            else
                return 0
            end
            """
            try:
                await self.redis.eval(lua, 1, key, token)
            except Exception:
                pass

    # 画图
    def draw(self, file_name: str = "健康管家图.png"):
        data = self.agent.get_graph().draw_mermaid_png()
        with open(file_name, "wb") as f:
            f.write(data)


if __name__ == "__main__":
    import asyncio

    # 自测：用内存 Checkpointer + 无 Redis 跑通一次问询
    from langgraph.checkpoint.memory import InMemorySaver

    async def test():
        agent = HealthGraphAgent(InMemorySaver(), None)
        question = "我最近血压怎么样？帮我生成周报，再告诉我中午吃什么"
        async for c in agent.chat(question, "u001", "s001"):
            print(c, end="")

    asyncio.run(test())
