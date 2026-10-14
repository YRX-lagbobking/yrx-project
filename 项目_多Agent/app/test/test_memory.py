"""
四层记忆自测：L1 窗口 / L2 摘要 / L3 长期 / L4 画像（FR-13 / AC-23、AC-24）

推荐运行方式（在项目根目录下执行）：
    python -m app.test.test_memory
    python app/test/test_memory.py

演示内容：
    1. WindowMemory（L1，Redis List）      —— save 多轮 → query 取回
    2. SummaryMemory（L2，PostgreSQL）     —— save 摘要 → query 取回
    3. LongMemory（L3，Milvus→Chroma→内存）—— save 事实 → query 语义检索
    4. ProfileMemory（L4，Redis Hash）     —— save 结构化画像 → query 组装文本
    5. SessionManager / MemoryManager      —— 四层组装成 system 提示词（PromptBuilder）

依赖：redis、psycopg_pool、pymilvus / chromadb（均为可选依赖）。
      任一存储不可用时，对应层会打印降级说明并继续（NFR-02）。
"""
import asyncio
import os
import sys

# ---------- 项目根目录加入 sys.path ----------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SESSION_ID = "s_test_memory_001"
USER_ID = "u001"


async def demo_window_memory() -> None:
    """L1 窗口记忆：Redis List，最近 N 轮原文，TTL 1 天"""
    print("\n" + "-" * 72)
    print("【L1】窗口记忆 WindowMemory（Redis List，rpush + ltrim，TTL 1 天）")
    try:
        from app.ai.agent.memory.save.window_memory import WindowMemory
    except Exception as e:
        print(f"     [跳过] 模块不可用：{e}")
        return
    try:
        mem = WindowMemory(SESSION_ID)
        await mem.save("user", "我今天血压怎么样？")
        await mem.save("assistant", "您今天血压 128/82，整体平稳。")
        await mem.save("user", "那我今天该吃什么药？")
        records = await mem.query()
        print(f"     写入 3 条，取回 {len(records)} 条：")
        for r in records:
            print(f"       [{r.get('role')}] {str(r.get('content'))[:50]}")
        if len(records) >= 2:
            print("     ✔ AC-23 依据：同一 session_id 能取回上一轮结论（多轮上下文）")
    except Exception as e:
        print(f"     [降级] Redis 不可用或实现未完成：{type(e).__name__}: {e}")
        print("           降级行为：窗口记忆为空，PromptBuilder 只用摘要+画像组装提示词。")


async def demo_summary_memory() -> None:
    """L2 摘要记忆：PostgreSQL，按 session_id upsert"""
    print("\n" + "-" * 72)
    print("【L2】摘要记忆 SummaryMemory（PostgreSQL，窗口消息 ≥ 2 条时增量更新）")
    try:
        from app.ai.agent.memory.save.summary_memory import SummaryMemory
    except Exception as e:
        print(f"     [跳过] 模块不可用：{e}")
        return
    try:
        from app.ai.agent.memory.save.summary_memory import pool

        if pool is not None:
            try:
                await pool.open()
            except Exception as e:
                print(f"     [降级] 连接池打开失败：{e}")
        mem = SummaryMemory(SESSION_ID)
        await mem.save("用户咨询了血压情况，结果为 128/82 平稳；并询问了今日用药。")
        summary = await mem.query()
        print(f"     取回摘要：{str(summary)[:120]}")
        if summary:
            print("     ✔ 摘要记忆可用（会话压缩后仍保留关键结论）")
    except Exception as e:
        print(f"     [降级] PostgreSQL 不可用或实现未完成：{type(e).__name__}: {e}")


async def demo_long_memory() -> None:
    """L3 长期记忆：Milvus 优先 → ChromaDB → 内存字典"""
    print("\n" + "-" * 72)
    print("【L3】长期记忆 LongMemory（Milvus → 降级 ChromaDB → 再降级内存字典）")
    try:
        from app.ai.agent.memory.save.long_memory import LongMemory
    except Exception as e:
        print(f"     [跳过] 模块不可用：{e}")
        return
    try:
        mem = LongMemory()
        await mem.save(USER_ID, "老人有高血压和2型糖尿病，对青霉素过敏，喜欢吃咸的。")
        await mem.save(USER_ID, "老人上午习惯去公园打太极，晚上八点吃降压药。")
        hits = await mem.query(USER_ID, "他的慢病史和过敏情况是什么？")
        print(f"     写入 2 条长期事实，按 user_id 过滤检索到 {len(hits)} 条：")
        for h in hits:
            print(f"       - {str(h)[:80]}")
        if hits:
            print("     ✔ 长期记忆可用（跨会话记住偏好与慢病史）")
    except Exception as e:
        print(f"     [降级] 向量库不可用或实现未完成：{type(e).__name__}: {e}")


async def demo_profile_memory() -> None:
    """L4 用户画像：Redis Hash，结构化属性"""
    print("\n" + "-" * 72)
    print("【L4】用户画像 ProfileMemory（Redis Hash，ProfileAgent 结构化抽取后 hset）")
    try:
        from app.ai.agent.memory.save.profile_memory import ProfileMemory
    except Exception as e:
        print(f"     [跳过] 模块不可用：{e}")
        return
    try:
        mem = ProfileMemory(USER_ID)
        await mem.save("name", "张三")
        await mem.save("age", 72)
        await mem.save("disease_history", "高血压,2型糖尿病")
        await mem.save("allergy", "青霉素")
        await mem.save("emergency_contact", "张小明")
        await mem.save("emergency_phone", "13900000001")
        await mem.save("communication_preference", "喜欢简短口语，语速慢一点")
        text = await mem.query()
        print(f"     画像文本：{str(text)[:200]}")
        if text:
            print("     ✔ 画像记忆可用（结构化属性会注入 system 提示词）")
    except Exception as e:
        print(f"     [降级] Redis 不可用或实现未完成：{type(e).__name__}: {e}")


async def demo_session_manager() -> None:
    """四层组装：SessionManager / MemoryManager / PromptBuilder"""
    print("\n" + "-" * 72)
    print("【组装】SessionManager + PromptBuilder：四层记忆拼成 system 提示词")
    try:
        from app.ai.agent.memory.manager.session_mananger import SessionManager
    except Exception as e:
        print(f"     [跳过] SessionManager 不可用：{e}")
        return
    try:
        sm = SessionManager(SESSION_ID, USER_ID)
        print(f"     持有组件：window={type(sm.window_memory).__name__} "
              f"summary={type(sm.summary_memory).__name__} "
              f"long={type(sm.long_memory).__name__} "
              f"profile={type(sm.profile_memory).__name__} "
              f"builder={type(sm.prompt_builder).__name__}")
        await sm.save("user", "我今天血压怎么样？")
        prompt = await sm.build_prompt(USER_ID, "我今天血压怎么样？")
        print(f"     组装出的提示词（前 240 字）：")
        print(f"       {str(prompt.get('content', prompt))[:240]}…")
        print("     ✔ 图执行前由 PromptBuilder 把四层记忆注入 system 提示词")
    except Exception as e:
        print(f"     [降级] 组装失败（存储不可用时允许降级为空提示词）：{type(e).__name__}: {e}")

    # 会话锁（AC-24）说明
    print("\n【会话隔离与并发】")
    print("     · Checkpointer 的 thread_id = session_id → 不同会话上下文互不干扰（AC-23）")
    print("     · Redis 键 session:<uuid> 存会话归属，校验 session_id 属于当前 user_id")
    print("     · Redis 键 lock:session:<session_id> 是分布式锁：同一会话并发第二次请求")
    print("       会被拒绝并提示\"会话忙\"（AC-24）")


async def main_async() -> None:
    print("=" * 72)
    print("四层记忆自测：L1 窗口 / L2 摘要 / L3 长期 / L4 画像（FR-13 / AC-23、AC-24）")
    print("=" * 72)
    try:
        from app.core.config import settings

        print(f"\n【0】记忆配置：WINDOW_MEMORY_ROUNDS={settings.window_memory_rounds} "
              f"WINDOW_MEMORY_TIME={settings.window_memory_time}")
        print(f"       Redis={settings.redis_host}:{settings.redis_port}/{settings.redis_db} "
              f"Milvus={settings.milvus_host}:{settings.milvus_port} "
              f"Chroma={settings.chroma_path}")
    except Exception:
        print("\n【0】配置不可读，使用默认值继续")

    await demo_window_memory()
    await demo_summary_memory()
    await demo_long_memory()
    await demo_profile_memory()
    await demo_session_manager()

    print("\n" + "=" * 72)
    print("四层记忆自测结束。降级总览（NFR-02）：")
    print("  L1 Redis 不可用     → 窗口为空，仍可对话（少一点上下文）")
    print("  L2 PostgreSQL 不可用 → 摘要为空，靠窗口记忆兜底")
    print("  L3 Milvus 不可用     → ChromaDB → 内存字典，接口保持可用")
    print("  L4 Redis 不可用     → 画像为空，从 MySQL health_profile 兜底读取")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main_async())
