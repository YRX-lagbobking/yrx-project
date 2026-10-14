"""
Redis 同步客户端（模块级单例）—— 记忆层统一入口

为什么是"同步客户端 + to_thread"而不是 redis.asyncio：
    1. 写法直观：`redis.StrictRedis(host=..., port=..., db=...)`，连接参数一目了然；
    2. 行为可控：不依赖 asyncio 版客户端的连接池/事件循环细节（Windows 上也少踩坑）；
    3. 用法不变：调用方仍是 async 接口，阻塞调用统一由 `asyncio.to_thread` 搬到线程池，
       不会卡住事件循环。

为什么放在 app/core：
    L1 窗口记忆、L4 用户画像（以及后续会话/锁）都用同一份连接配置，
    集中在这里可以保证"一个进程一个客户端"，避免每个记忆实例都新建客户端累积连接。

降级约定（NFR-02）：
    Redis 未安装 / 连不上 / 命令报错 → 返回 None，由调用方降级为进程内字典；
    连接失败后有冷却时间（默认 10 秒），避免每次调用都白等一次 TCP 超时。
"""
import time
from typing import Any

from app.core.config import settings
from app.core.logger import logger

# 模块级单例（一个进程一个客户端）
_client: Any = None
# 上次连接失败时间（用于失败冷却，避免反复卡在 TCP 超时上）
_last_fail_at: float = 0.0
# 连接失败后的冷却秒数
RETRY_INTERVAL = 10.0


def redis_desc() -> str:
    """返回当前连接描述（日志用，不含密码）"""
    return f"{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"


def get_redis():
    """
    获取（惰性创建）全局唯一的同步 Redis 客户端。

    返回 None 表示当前不可用（未安装 / 连不上 / 处于失败冷却期），调用方应走降级路径。
    """
    global _client, _last_fail_at
    if _client is not None:
        return _client
    # 失败冷却：刚失败过就先不重试，直接走降级，避免每次记忆读写都卡 3 秒
    if _last_fail_at and (time.time() - _last_fail_at) < RETRY_INTERVAL:
        return None
    try:
        import redis  # 延迟导入：未安装时只影响记忆层，不影响服务启动

        client = redis.StrictRedis(
            host=settings.redis_host,            # .env：REDIS_HOST=localhost
            port=settings.redis_port,            # .env：REDIS_PORT=6379
            db=settings.redis_db,                # .env：REDIS_DB=1
            password=settings.redis_password or None,
            decode_responses=True,               # 直接返回 str，中文不乱码、不用手动 decode
            socket_connect_timeout=3,            # 连不上就快速失败，不要拖住请求
            socket_timeout=3,
            health_check_interval=30,            # 空闲连接自动探活
        )
        client.ping()                            # 立刻验证连通性（配置错在这里就暴露）
        _client = client
        _last_fail_at = 0.0
        logger.info(f"Redis 连接成功（同步客户端）：{redis_desc()}")
        return _client
    except Exception as e:  # noqa: BLE001
        _last_fail_at = time.time()
        logger.warning(f"Redis 连接失败（{redis_desc()}），记忆降级为进程内字典：{type(e).__name__}: {e}")
        return None


def reset_client() -> None:
    """丢弃当前客户端（连接异常/测试用），下次调用会重新建立并探活"""
    global _client, _last_fail_at
    _client = None
    _last_fail_at = 0.0


def ping() -> bool:
    """探活：True 表示 Redis 可用"""
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(client.ping())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Redis 探活失败：{e}")
        reset_client()
        return False


if __name__ == "__main__":
    print("连接描述：", redis_desc())
    print("探活：", ping())
    client = get_redis()
    if client is not None:
        print("DBSIZE：", client.dbsize())
        print("window_memory 键（最多 5 个）：", (client.keys("window_memory:*") or [])[:5])
