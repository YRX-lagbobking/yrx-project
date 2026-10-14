"""
会话索引 DAO（PostgreSQL）—— 支撑前端"历史记录列表"的持久化

为什么需要它：
    前端的历史列表原来只存在浏览器内存里（刷新即清空），Redis 的
    session:{uuid} 又有 1 天 TTL，过期后 check_session 会判定"会话不存在"，
    用户隔天回来就被强制新建会话。本模块给会话一个**永久索引**。

存储设计（与项目其它部分的边界保持一致）：
    · PostgreSQL chat_session 表：session_id / user_id / title / create_time / update_time
    · 消息正文不在这里存 —— 由 LangGraph Checkpointer（checkpoints 表，
      thread_id = session_id）保存完整消息序列，本表只做"列表 + 归属 + 标题"。
    · 用**同步 psycopg + asyncio.to_thread**：psycopg 同步版不受事件循环类型影响
      （异步版在 Windows ProactorEventLoop 下会被拒绝），最稳。

降级约定（NFR-02）：
    PostgreSQL 不可用时，所有方法只记录日志并返回"空/False"，
    前端历史列表退回"仅本次会话内存可见"，对话主链路完全不受影响。
"""
import asyncio
import time
from typing import Any

from app.core.config import settings
from app.core.logger import logger

# 表名与默认标题（前端"新的对话"与后端保持同一文案）
TABLE = "chat_session"
DEFAULT_TITLE = "新的对话"
# 标题最大长度（列表展示用，超出截断）
TITLE_MAX = 18

# 建表是否已成功（成功一次后不再重复建，避免每次请求都额外开销）
_ENSURED = False


# ---------------------------------------------------------------------------
# 连接与建表
# ---------------------------------------------------------------------------
def _connect():
    """建立同步 psycopg 连接（row_factory=dict_row，返回 dict 便于直接序列化）"""
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(
        settings.postgresql_url,
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=5,
    )


def ensure_table() -> bool:
    """
    自愈建表（幂等）：ChatSession 表 + 用户维度索引。

    为什么自愈：项目里 PG 只用于 Checkpointer / 摘要记忆，没有统一迁移工具；
    建表放代码里可以保证"谁先跑起来谁建表"，避免依赖手工执行 SQL。
    """
    global _ENSURED
    if _ENSURED:
        return True
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                    session_id  TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    title       TEXT NOT NULL DEFAULT '{DEFAULT_TITLE}',
                    create_time DOUBLE PRECISION NOT NULL,
                    update_time DOUBLE PRECISION NOT NULL
                )
                """
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_user_time "
                f"ON {TABLE} (user_id, update_time DESC)"
            )
        _ENSURED = True
        logger.info(f"会话索引表 {TABLE} 就绪（PostgreSQL）")
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"会话索引表初始化失败（历史列表降级为前端内存）：{e}")
        return False


# ---------------------------------------------------------------------------
# 同步实现（在 asyncio.to_thread 中执行）
# ---------------------------------------------------------------------------
def _create_sync(session_id: str, user_id: str, title: str) -> bool:
    if not ensure_table():
        return False
    now = time.time()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {TABLE} (session_id, user_id, title, create_time, update_time)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET user_id = EXCLUDED.user_id
            """,
            (session_id, user_id, title, now, now),
        )
    return True


def _touch_sync(session_id: str) -> bool:
    if not ensure_table():
        return False
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {TABLE} SET update_time = %s WHERE session_id = %s", (time.time(), session_id))
        return cur.rowcount > 0


def _set_title_sync(session_id: str, title: str) -> bool:
    """只有标题还是默认值时才写入（避免后续提问覆盖首条标题）"""
    if not ensure_table():
        return False
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {TABLE} SET title = %s, update_time = %s "
            f"WHERE session_id = %s AND (title IS NULL OR title = '' OR title = %s)",
            (title, time.time(), session_id, DEFAULT_TITLE),
        )
        return cur.rowcount > 0


def _list_sync(user_id: str, limit: int) -> list[dict]:
    if not ensure_table():
        return []
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT session_id, user_id, title, create_time, update_time "
            f"FROM {TABLE} WHERE user_id = %s ORDER BY update_time DESC LIMIT %s",
            (str(user_id), int(limit)),
        )
        return [dict(r) for r in cur.fetchall()]


def _get_sync(session_id: str) -> dict | None:
    if not ensure_table():
        return None
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT session_id, user_id, title, create_time, update_time "
            f"FROM {TABLE} WHERE session_id = %s",
            (session_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _delete_sync(session_id: str, user_id: str) -> bool:
    if not ensure_table():
        return False
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {TABLE} WHERE session_id = %s AND user_id = %s", (session_id, str(user_id)))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# 对外异步接口（异常一律吞掉并降级，绝不打断对话主链路）
# ---------------------------------------------------------------------------
async def _run(func, *args) -> Any:
    """把同步 psycopg 调用丢进线程池，避免阻塞事件循环；异常降级"""
    try:
        return await asyncio.to_thread(func, *args)
    except Exception as e:  # noqa: BLE001
        logger.error(f"会话索引操作失败（已降级，不影响对话）：{func.__name__}: {e}")
        return False if func is not _list_sync else []


def make_title(question: str) -> str:
    """由首条提问生成会话标题（去空白 + 截断，前端列表展示用）"""
    text = " ".join(str(question or "").split())
    if not text:
        return DEFAULT_TITLE
    return text if len(text) <= TITLE_MAX else text[:TITLE_MAX] + "…"


async def create_session(session_id: str, user_id: str, title: str = DEFAULT_TITLE) -> bool:
    """新建会话索引（已存在则只校正 user_id，不覆盖标题与时间）"""
    return await _run(_create_sync, str(session_id), str(user_id), str(title))


async def touch_session(session_id: str) -> bool:
    """刷新会话活跃时间（每轮对话后调用，决定历史列表排序）"""
    return await _run(_touch_sync, str(session_id))


async def set_title_if_default(session_id: str, title: str) -> bool:
    """标题仍是"新的对话"时，用首条提问补上标题"""
    return await _run(_set_title_sync, str(session_id), str(title))


async def list_sessions(user_id: str, limit: int = 50) -> list[dict]:
    """列出某用户的历史会话（按最后活跃时间倒序）"""
    return await _run(_list_sync, str(user_id), int(limit))


async def get_session(session_id: str) -> dict | None:
    """取单个会话索引（用于历史记录接口的归属校验）"""
    row = await _run(_get_sync, str(session_id))
    return row if isinstance(row, dict) else None


async def delete_session(session_id: str, user_id: str) -> bool:
    """删除会话索引（消息正文仍留在 Checkpointer 中，如需彻底删除需另删 thread）"""
    return await _run(_delete_sync, str(session_id), str(user_id))


# ---------------------------------------------------------------------------
# 历史消息（前端"历史记录列表"的消息来源）
# ---------------------------------------------------------------------------
MSG_TABLE = "chat_message"
_MSG_ENSURED = False


def ensure_message_table() -> bool:
    """自愈建表（幂等）：历史消息表 chat_message（PG 不可用时返回 False，历史回退 Checkpointer）"""
    global _MSG_ENSURED
    if _MSG_ENSURED:
        return True
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {MSG_TABLE} (
                    id          BIGSERIAL PRIMARY KEY,
                    session_id  TEXT NOT NULL,
                    user_id     TEXT,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    create_time DOUBLE PRECISION NOT NULL
                )
                """
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{MSG_TABLE}_session ON {MSG_TABLE} (session_id, id)")
        _MSG_ENSURED = True
        logger.info(f"历史消息表 {MSG_TABLE} 就绪（PostgreSQL）")
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"历史消息表初始化失败（历史记录回退 Checkpointer）：{e}")
        return False


def _append_message_sync(session_id: str, role: str, content: str, user_id: str) -> int:
    """写一条历史消息（role: user / bot），返回新 id"""
    if not ensure_message_table():
        return 0
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {MSG_TABLE} (session_id, user_id, role, content, create_time) "
            f"VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (str(session_id), str(user_id), str(role), str(content), time.time()),
        )
        row = cur.fetchone() or {}
        return int(row.get("id") or 0)


def _list_messages_sync(session_id: str) -> list[dict]:
    """按时间正序列出该会话的全部历史消息"""
    if not ensure_message_table():
        return []
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id, role, content, create_time FROM {MSG_TABLE} "
            f"WHERE session_id = %s ORDER BY id",
            (str(session_id),),
        )
        return list(cur.fetchall() or [])


def _delete_messages_sync(session_id: str) -> int:
    """删除该会话的全部历史消息，返回删除行数"""
    if not ensure_message_table():
        return 0
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {MSG_TABLE} WHERE session_id = %s", (str(session_id),))
        return int(cur.rowcount or 0)


async def append_message(session_id: str, role: str, content: str, user_id: str = "") -> int:
    """追加一条历史消息（PG 不可用时返回 0，不影响对话）"""
    row_id = await _run(_append_message_sync, str(session_id), str(role), str(content), str(user_id))
    return int(row_id or 0)


async def list_messages(session_id: str) -> list[dict]:
    """该会话的全部历史消息 [{"id","role","content","create_time"}]（正序；失败返回 []）"""
    rows = await _run(_list_messages_sync, str(session_id))
    return rows if isinstance(rows, list) else []


async def delete_messages(session_id: str) -> int:
    """删除该会话的全部历史消息，返回删除行数"""
    count = await _run(_delete_messages_sync, str(session_id))
    return int(count or 0)


if __name__ == "__main__":
    import asyncio as _asyncio

    async def test():
        print("建表：", ensure_table())
        sid = f"self_test_{int(time.time())}"
        print("写入：", await create_session(sid, "self_test_u001", "新的对话"))
        print("补标题：", await set_title_if_default(sid, make_title("我今天血压有点高怎么办")))
        print("刷新时间：", await touch_session(sid))
        rows = await list_sessions("self_test_u001")
        print(f"列表（{len(rows)} 条）：", rows[:3])
        print("单条：", await get_session(sid))
        print("删除：", await delete_session(sid, "self_test_u001"))

    _asyncio.run(test())
