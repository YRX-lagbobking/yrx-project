"""
四层记忆 · L2 摘要记忆（PostgreSQL）

职责：
    保存"本次会话的压缩摘要"，让模型知道"这次已经聊过什么"，避免重复问、重复建议。

表结构（sql/init_postgres.sql 中定义；首次读写时也会自动建表兜底）：
    conversation_summary(
        session_id  TEXT PRIMARY KEY,
        summary     TEXT,
        update_time TIMESTAMPTZ DEFAULT NOW()
    )

连接池约定（【锁定】接口）：
    pool 必须是**模块级变量**，因为 app/main.py 在 lifespan 里这样用：
        from app.ai.agent.memory.save.summary_memory import pool
        await pool.open()   /   await pool.close()
    因此：
        1. pool 一定存在于模块命名空间（未安装 psycopg_pool 时为 None）；
        2. 构造时 open=False（不在导入期建连，否则导入即阻塞、启动更慢）；
        3. 连接串从 settings.pg_* 拼装，禁止在业务模块散落 os.getenv。

降级设计：
    psycopg_pool 未安装 / PostgreSQL 不可用 / SQL 报错 → 自动降级为进程内字典
    （模块级 _FALLBACK_SUMMARY），query 返回 "" 而不是抛异常，保证对话主流程不中断。
"""
from app.core.config import settings
from app.core.logger import logger

# 进程内降级存储：{session_id: summary}，模块级，与 Redis/PG 的"跨实例共享"语义对齐
_FALLBACK_SUMMARY: dict = {}


class _AsyncPoolProxy:
    """
    连接池兼容代理。

    背景：psycopg_pool 不同大版本的"打开/关闭"调用方式不同：
        - 新版：await pool.open() / await pool.close()
        - 旧版：pool.open()（内部 wait()）/ pool.close() 为同步方法
    本项目 main.py 统一写 `await pool.open()`，故这里做一层薄代理，
    两种版本都能被 `await`，避免"启动期报错 → 摘要记忆整体降级"。
    """

    def __init__(self, pool_obj):
        # 真实连接池对象
        self._pool = pool_obj

    async def open(self, *args, **kwargs):
        """打开连接池：优先按协程处理，退化为同步调用"""
        result = self._pool.open(*args, **kwargs)
        if hasattr(result, "__await__"):
            await result
        else:
            # 旧版同步 open() 后需要显式等待就绪
            wait = getattr(self._pool, "wait", None)
            if callable(wait):
                try:
                    wait()
                except Exception:
                    pass

    async def close(self, *args, **kwargs):
        """关闭连接池：优先按协程处理，退化为同步调用"""
        result = self._pool.close(*args, **kwargs)
        if hasattr(result, "__await__"):
            await result

    def __getattr__(self, name):
        """其余属性/方法（connection() 等）透传给真实连接池"""
        return getattr(self._pool, name)


def _build_pool():
    """
    模块导入时构造异步连接池（open=False，不真正建连）。

    构造失败（未安装 psycopg_pool、参数非法等）返回 None → SummaryMemory 自动降级。
    """
    try:
        from psycopg_pool import AsyncConnectionPool

        return _AsyncPoolProxy(
            AsyncConnectionPool(
                # 连接串从 settings.pg_* 拼装（与 settings.postgresql_url 同源，只是用 DSN 关键字写法）
                conninfo=(
                    f"host={settings.pg_host} port={settings.pg_port} dbname={settings.pg_db} "
                    f"user={settings.pg_user} password={settings.pg_password} "
                    f"connect_timeout=3"
                ),
                # 最大/最小连接数：教学项目规模，20 足够支撑并发会话
                max_size=20,
                # 最小保活连接数
                min_size=1,
                # 导入期不建连，由 main.py 的 lifespan 负责 await pool.open()
                open=False,
                # 从池里取连接的等待超时（秒）
                timeout=5,
            )
        )
    except Exception as e:
        logger.warning(f"摘要记忆连接池构造失败（未安装 psycopg_pool 或配置非法），将降级为内存字典：{e}")
        return None


# 模块级连接池：未安装 psycopg_pool 时为 None（main.py 依赖这个常量，缺失会导致启动失败）
pool = _build_pool()


# =============================================================================
# 建表兜底（自愈）
# -----------------------------------------------------------------------------
# 为什么需要：摘要记忆写的是 **PostgreSQL**，而 sql/init.sql 是 MySQL 脚本，
# 两者不是同一个库。若只在 MySQL 建了 conversation_summary，PG 里就没有这张表，
# 摘要写入会一直报"表不存在"并静默降级到内存字典（重启即丢）。
# 因此这里在首次读写前执行一次 CREATE TABLE IF NOT EXISTS：
#   1. 新环境零手工步骤即可用；
#   2. sql/init_postgres.sql 仍然保留，便于 DBA 预先建表 / 审计。
# =============================================================================
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_summary (
    session_id  TEXT PRIMARY KEY,
    summary     TEXT,
    update_time TIMESTAMPTZ DEFAULT NOW()
)
"""
_TABLE_READY = False
_TABLE_LOCK = None


def _get_table_lock():
    """懒创建 asyncio.Lock（避免导入期就绑定事件循环）"""
    global _TABLE_LOCK
    if _TABLE_LOCK is None:
        import asyncio

        _TABLE_LOCK = asyncio.Lock()
    return _TABLE_LOCK


async def ensure_table() -> bool:
    """确保 conversation_summary 表存在；返回是否就绪（pool 为 None 时返回 False）"""
    global _TABLE_READY
    if pool is None:
        return False
    if _TABLE_READY:
        return True
    async with _get_table_lock():
        if _TABLE_READY:  # 双重检查，避免并发重复建表
            return True
        try:
            async with pool.connection() as con:
                async with con.cursor() as cur:
                    await cur.execute(_CREATE_TABLE_SQL)
                await con.commit()
            _TABLE_READY = True
            logger.info("摘要记忆表 conversation_summary 已就绪（CREATE TABLE IF NOT EXISTS）")
        except Exception as e:
            logger.error(f"摘要记忆表初始化失败（将降级为内存字典）：{e}")
    return _TABLE_READY



class SummaryMemory:
    """L2 摘要记忆：PostgreSQL 按 session_id upsert，不可用时降级为进程内字典"""

    def __init__(self, session_id: str):
        self.session_id = str(session_id)

    # ---------------- 保存 ----------------
    async def save(self, summary: str) -> None:
        """写入/更新摘要（ON CONFLICT 做 upsert，一个会话只保留一条最新摘要）"""
        text = "" if summary is None else str(summary)
        if pool is not None:
            # 首次写入前确保表存在（自愈：PostgreSQL 里没建过表也不会静默丢摘要）
            await ensure_table()
            try:
                async with pool.connection() as con:
                    async with con.cursor() as cur:
                        await cur.execute(
                            """
                            INSERT INTO conversation_summary(session_id, summary)
                            VALUES(%s, %s)
                            ON CONFLICT(session_id)
                            DO UPDATE SET summary = EXCLUDED.summary, update_time = NOW()
                            """,
                            (self.session_id, text),
                        )
                    # 显式提交（连接池不会自动 commit）
                    await con.commit()
                return
            except Exception as e:  # 数据库不可用 / 表不存在 / 连接超时
                logger.error(f"摘要记忆写入 PostgreSQL 失败，降级为进程内字典：{e}")

        # ---------- 降级路径 ----------
        _FALLBACK_SUMMARY[self.session_id] = text

    # ---------------- 查询 ----------------
    async def query(self) -> str:
        """读取摘要；没有摘要或数据库不可用时返回空字符串"""
        if pool is not None:
            await ensure_table()
            try:
                async with pool.connection() as con:
                    async with con.cursor() as cur:
                        await cur.execute(
                            "SELECT summary FROM conversation_summary WHERE session_id = %s",
                            (self.session_id,),
                        )
                        rs = await cur.fetchone()
                        return rs[0] if rs and rs[0] else ""
            except Exception as e:
                logger.error(f"摘要记忆读取 PostgreSQL 失败，改用进程内字典：{e}")

        # ---------- 降级路径 ----------
        return _FALLBACK_SUMMARY.get(self.session_id, "")


if __name__ == "__main__":
    import asyncio

    async def test():
        # 自测：不依赖数据库也能跑通（PostgreSQL 不可用时走进程内字典降级）
        print("连接池是否可用：", pool is not None)
        memory = SummaryMemory("self_test_001")
        await memory.save("老人主诉头晕，已建议休息并复测血压；既往高血压，正在服用氨氯地平。")
        rs = await memory.query()
        print("摘要记忆查询结果：", rs)

    asyncio.run(test())
