"""
四层记忆 · L1 窗口记忆（Redis List）

职责：
    保存"最近 N 轮对话原文"，让模型知道"这一句在说什么"。

数据结构：
    key   : window_memory:{session_id}
    value : Redis List，元素为 JSON 字符串 {"role":"user|ai","content":"..."}
    写入  : rpush（尾部追加，天然按时间有序）
    裁剪  : ltrim(-window_memory_rounds, -1) 只保留最近 N 条
    过期  : expire(window_memory_time) 默认 1 天，避免冷会话长期占内存

客户端说明（本次改造）：
    原来每个 WindowMemory 实例都会 `redis.asyncio.StrictRedis(...)` 新建一个**异步**客户端；
    现在统一走 app/core/redis_client.py 的**同步**客户端单例：
        redis.StrictRedis(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db, ...)
    阻塞调用用 asyncio.to_thread 搬到线程池，因此本类的 async 接口（save/query）保持不变，
    调用方（SessionManager / PromptBuilder）无需任何改动。
    好处：一个进程只建一个客户端，不再随会话数量累积连接；decode_responses=True 也不用再手动 decode。

降级设计（NFR-02）：
    redis 未安装 / 连不上 / 命令报错 → 自动降级为"进程内字典"（模块级 _FALLBACK），
    接口签名与返回结构完全不变，保证记忆层永远可用。
    注意：内存降级只在单进程内有效，服务重启即丢失，属于"能用优先"的妥协。
"""
import asyncio
import json

from app.core.config import settings
from app.core.logger import logger
from app.core.redis_client import get_redis

# 进程内降级存储：{key: [json字符串, ...]}，模块级，保证跨实例共享（与窗口记忆语义一致）
_FALLBACK: dict = {}


class WindowMemory:
    """L1 窗口记忆：Redis List（同步客户端 + to_thread），不可用时降级为进程内字典"""

    def __init__(self, session_id: str):
        # 会话编号：一次会话（一个 thread_id）对应一条 Redis List
        self.session_id = str(session_id)
        # Redis 键名：window_memory:{session_id}
        self.key = f"window_memory:{self.session_id}"
        # 保留条数（默认 10 条）/ 过期时间（默认 86400 秒 = 1 天）
        self.window_size = settings.window_memory_rounds
        self.window_limit_time = settings.window_memory_time
        # 注意：构造期不建连（保持 SessionManager "构造期不访问任何存储"的约定），调用时惰性获取

    # ---------------- 保存 ----------------
    async def save(self, role: str, content: str) -> None:
        """追加一条对话（rpush + ltrim + expire）；Redis 不可用时写内存字典"""
        # 统一转字符串，避免上游传入 None / 非字符串
        data = json.dumps({"role": str(role), "content": "" if content is None else str(content)},
                          ensure_ascii=False)
        client = get_redis()
        if client is not None:
            try:
                # 阻塞式 Redis 调用放线程池，避免卡住事件循环
                await asyncio.to_thread(
                    self._save_sync, client, self.key, data,
                    int(self.window_size), int(self.window_limit_time),
                )
                return
            except Exception as e:  # 连接断开 / 命令失败 → 降级
                logger.error(f"窗口记忆写入 Redis 失败，降级为进程内字典：{e}")

        # ---------- 降级路径：进程内字典 ----------
        items = _FALLBACK.setdefault(self.key, [])
        items.append(data)
        # 与 Redis 的 ltrim 保持一致的裁剪语义
        if len(items) > int(self.window_size):
            del items[: len(items) - int(self.window_size)]

    @staticmethod
    def _save_sync(client, key: str, data: str, size: int, ttl: int) -> None:
        """同步写入：用 pipeline 把 rpush/ltrim/expire 合成一次往返"""
        pipe = client.pipeline()
        pipe.rpush(key, data)
        pipe.ltrim(key, -size, -1)
        pipe.expire(key, ttl)
        pipe.execute()

    # ---------------- 查询 ----------------
    async def query(self) -> list:
        """读取窗口记忆，返回 [{"role","content"}]（按时间正序）；无数据返回 []"""
        client = get_redis()
        if client is not None:
            try:
                raw = await asyncio.to_thread(self._query_sync, client, self.key)
                return [r for r in (self._decode(item) for item in raw) if r]
            except Exception as e:
                logger.error(f"窗口记忆读取 Redis 失败，改用进程内字典：{e}")

        # ---------- 降级路径 ----------
        return [r for r in (self._decode(item) for item in _FALLBACK.get(self.key, [])) if r]

    @staticmethod
    def _query_sync(client, key: str) -> list:
        """同步读取：lrange 0 -1（decode_responses=True，元素已是 str）"""
        return client.lrange(key, 0, -1) or []

    # ---------------- 内部：解码 ----------------
    @staticmethod
    def _decode(item):
        """把 Redis 取出的 bytes / str 解析成 dict；非法内容返回 None（跳过）"""
        try:
            if isinstance(item, (bytes, bytearray)):
                item = item.decode("utf-8")
            return json.loads(item)
        except Exception:  # noqa: BLE001
            return None


if __name__ == "__main__":
    import asyncio

    async def test():
        # 自测：写入 3 轮对话再查询（Redis 不可用时会自动走进程内字典降级）
        from app.core.redis_client import ping, redis_desc

        print("Redis 连接：", redis_desc(), "可用 =" , ping())
        memory = WindowMemory("self_test_001")
        await memory.save("user", "你好，我今天有点头晕")
        await memory.save("ai", "您好，请先坐下休息，我帮您看看最近的血压")
        await memory.save("user", "我早上量了血压 150/95")
        await memory.save("ai", "收缩压偏高，建议连续测量并联系家属")
        rs = await memory.query()
        print(f"窗口记忆条数：{len(rs)}")
        for item in rs:
            print("  ", item)
        # 清理自测键，避免污染 Redis
        client = get_redis()
        if client is not None:
            client.delete(memory.key)
            print("已清理自测键：", memory.key)

    asyncio.run(test())
