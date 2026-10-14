"""
四层记忆 · L4 用户画像记忆（Redis Hash）

职责：
    保存"结构化属性"（姓名/年龄/慢病/过敏/紧急联系人/沟通偏好/身体弱点），
    让模型每轮都知道"这位老人是谁、要特别注意什么、该怎么和他说话"。

数据结构：
    key   : profile:{user_id}
    value : Redis Hash，field = hashkey（如 disease_history），value = 字符串

为什么用 Hash 而不是 JSON 字符串：
    画像字段是"逐个抽取、逐个更新"的（本轮可能只抽到"过敏"，下一轮只抽到"慢病"），
    Hash 的 hset 天然支持单字段增量更新，不需要"读-改-写"整个对象，避免并发覆盖。

客户端说明（与 L1 窗口记忆统一）：
    统一走 app/core/redis_client.py 的**同步**客户端单例
    （redis.StrictRedis(host=..., port=..., db=..., decode_responses=True)），
    阻塞调用用 asyncio.to_thread 搬到线程池，本类 async 接口不变。

本次同时修复的缺陷：
    原 query() 在 `for hashkey, value in rs.items(): data[key] = val` 里 **没有先初始化 data**，
    只要 Redis 里已有画像数据就会抛 NameError，被 except 吞掉后返回空字符串
    → 表现是"用户画像一直读不出来"（提示词里的【L4 用户画像】是空的）。
    现已改为先 `data = {}` 再填充。

降级设计：
    redis 未安装 / 连不上 / 命令报错 → 自动降级为进程内字典（模块级 _FALLBACK_PROFILE），
    接口签名与返回格式（"key:value\\n" 文本）完全不变。
"""
import asyncio

from app.core.logger import logger
from app.core.redis_client import get_redis

# 进程内降级存储：{profile:{user_id}: {hashkey: value}}
_FALLBACK_PROFILE: dict = {}


class ProfileMemory:
    """L4 用户画像记忆：Redis Hash，不可用时降级为进程内字典"""

    def __init__(self, user_id):
        user_id = str(user_id)
        # 画像键：profile:{user_id}
        self.key = f"profile:{user_id}"
        # 构造期不建连，调用时惰性获取（保持"构造期不访问任何存储"的约定）

    # ---------------- 保存 ----------------
    async def save(self, hashkey: str, value) -> None:
        """增量写入一个画像字段（hset）；Redis 不可用时写内存字典"""
        # 画像值统一转字符串：Redis Hash 只能存字符串/字节
        field = str(hashkey)
        text = "" if value is None else str(value)
        client = get_redis()
        if client is not None:
            try:
                await asyncio.to_thread(client.hset, self.key, field, text)
                return
            except Exception as e:  # noqa: BLE001
                logger.error(f"用户画像写入 Redis 失败，降级为进程内字典：{e}")

        # ---------- 降级路径 ----------
        _FALLBACK_PROFILE.setdefault(self.key, {})[field] = text

    # ---------------- 查询 ----------------
    async def query(self) -> str:
        """读取全部画像字段，拼成 "key:value\\n" 文本；无画像返回空字符串"""
        client = get_redis()
        if client is not None:
            try:
                raw = await asyncio.to_thread(client.hgetall, self.key)
                # ⚠️ 必须先初始化：原实现在这里漏了 data = {}，导致有数据时抛 NameError
                data: dict = {}
                for hashkey, value in (raw or {}).items():
                    # decode_responses=True 时已是 str；兼容 False 的情况手动解码
                    key = hashkey.decode() if isinstance(hashkey, (bytes, bytearray)) else str(hashkey)
                    val = value.decode() if isinstance(value, (bytes, bytearray)) else str(value)
                    data[key] = val
                # "像文本一样"的拼接结果，便于直接塞进提示词
                return "".join(f"{k}:{v}\n" for k, v in data.items())
            except Exception as e:  # noqa: BLE001
                logger.error(f"用户画像读取 Redis 失败，改用进程内字典：{e}")

        # ---------- 降级路径 ----------
        data = _FALLBACK_PROFILE.get(self.key, {})
        return "".join(f"{k}:{v}\n" for k, v in data.items())


if __name__ == "__main__":
    import asyncio as _asyncio

    async def test():
        from app.core.redis_client import ping, redis_desc

        print("Redis 连接：", redis_desc(), "可用 =", ping())
        memory = ProfileMemory("self_test_u001")
        # 模拟 ProfileAgent 逐字段写入
        await memory.save("name", "张桂芬")
        await memory.save("age", 78)
        await memory.save("disease_history", "高血压、2 型糖尿病")
        await memory.save("allergy", "青霉素过敏")
        await memory.save("emergency_contact", "儿子张伟")
        await memory.save("emergency_phone", "13800000000")
        await memory.save("communication_preference", "说慢一点，喜欢用方言")
        await memory.save("weaknesses", "听力下降、腿脚不便")
        rs = await memory.query()
        print("用户画像：")
        print(rs)
        # 清理自测键
        client = get_redis()
        if client is not None:
            client.delete(memory.key)
            print("已清理自测键：", memory.key)

    _asyncio.run(test())
