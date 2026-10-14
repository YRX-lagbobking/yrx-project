"""
四层记忆 · L3 长期记忆（Milvus → ChromaDB → 内存字典，三级降级）

职责：
    保存"跨会话的稳定事实"（慢病史、过敏、长期用药、家庭情况、沟通偏好），
    每次问询时按 user_id 过滤检索 Top3，注入提示词。

为什么需要长期记忆：
    独居老人可能换会话、换设备，L1 窗口和 L2 摘要都随会话消失；
    而"高血压 + 对青霉素过敏 + 儿子在外地"这类事实跨会话都有效，
    必须持久化下来，否则模型每轮都像第一次见面。

三级降级（本文就是"工具降级"的示例实现）：
    第 1 级 Milvus：向量库，语义检索效果最好（需要 pymilvus + 可用的 Milvus 服务）；
    第 2 级 ChromaDB：本地嵌入式向量库，免部署，语义效果与 Milvus 相当（需要 chromadb）；
    第 3 级 内存字典：dict[user_id] -> list[str]，用最朴素的关键词重合度排序，
                     无任何外部依赖，保证"接口永远可用"。
    三级都写全，任何一级不可用自动往下走，对上层调用方完全透明。

实现约定：
    - 构造 LongMemory() **不抛异常、不阻塞**：连接失败只记日志并标记该级不可用；
    - Milvus / Chroma 都是阻塞型客户端，必须用
      asyncio.get_event_loop().run_in_executor(None, lambda: ...) 包起来，
      否则会卡死事件循环（FastAPI 的 WebSocket / SSE 全部受阻）。
"""
import asyncio
import os
import uuid

from app.core.config import settings
from app.core.logger import logger

# 第三级降级：进程内长期记忆 {user_id: [文本, ...]}，模块级保证跨实例可见
_FALLBACK_LONG: dict = {}
# 内存降级时最多保留多少条（防止长驻进程无限增长）
_FALLBACK_MAX = 200


class LongMemory:
    """L3 长期记忆：Milvus 优先 → ChromaDB 降级 → 内存字典兜底"""

    def __init__(self):
        # 各级存储对象/标记：初始化失败的那一级保持 None
        self.milvus_collection = None
        self.chroma_collection = None
        self.chroma_client = None
        # 按 Milvus → ChromaDB 的顺序尝试接入，任一成功即停止
        self._init_milvus()
        if self.milvus_collection is None:
            self._init_chroma()
        # 日志说清当前落在哪一级，便于线上排查"为什么检索不准"
        if self.milvus_collection is not None:
            logger.info(f"长期记忆使用 Milvus 集合：{settings.long_memory_collection}")
        elif self.chroma_collection is not None:
            logger.info("长期记忆降级使用 ChromaDB 集合：long_memory")
        else:
            logger.warning("Milvus 与 ChromaDB 均不可用，长期记忆降级为进程内字典")

    # ---------------- 第 1 级：Milvus ----------------
    def _init_milvus(self):
        """初始化 Milvus 集合（阻塞调用，仅在构造期执行一次；失败则返回，留给下一级）"""
        try:
            from pymilvus import DataType, MilvusClient  # 延迟导入：未安装 pymilvus 不影响导入本模块

            # 显式传入向量维度，避免"先有鸡还是先有蛋"（建集合要先知道维度）
            dim = self._embedding_dim()
            client = MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}")
            name = settings.long_memory_collection
            if not client.has_collection(name):
                # 用 schema 建集合：主键 + 向量 + user_id 标量字段（用于按用户过滤）
                schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=False)
                schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
                schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
                schema.add_field(field_name="user_id", datatype=DataType.VARCHAR, max_length=64)
                schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=2048)
                index_params = client.prepare_index_params()
                # 长期记忆数据量小，HNSW + COSINE 足够
                index_params.add_index(field_name="vector", index_type="HNSW", metric_type="COSINE")
                client.create_collection(collection_name=name, schema=schema, index_params=index_params)
            self.milvus_collection = client
        except Exception as e:
            logger.warning(f"Milvus 不可用，长期记忆准备降级：{e}")
            self.milvus_collection = None

    def _embedding_dim(self) -> int:
        """取向量维度（优先真实探测；失败时用配置 EMBEDDING_DIM，默认 768 = bge-base-zh-v1.5）"""
        try:
            from app.ai.model.my_model import MyModel

            model = MyModel.get_embedding_model()
            if model is None:
                return int(getattr(settings, "embedding_dim", 768))
            vec = model.embed_query("维度探测")
            return len(vec)
        except Exception as e:
            logger.warning(f"向量维度探测失败，按配置 EMBEDDING_DIM 兜底：{e}")
            return int(getattr(settings, "embedding_dim", 768))

    # ---------------- 第 2 级：ChromaDB ----------------
    def _init_chroma(self):
        """初始化 ChromaDB 持久化集合（本地嵌入式，免部署；失败则留给内存兜底）"""
        try:
            import chromadb  # 延迟导入

            path = settings.chroma_path
            # 目录不存在时 chromadb 会自行创建，但父目录缺失时可能失败，这里先确保
            try:
                os.makedirs(path, exist_ok=True)
            except Exception:
                pass
            self.chroma_client = chromadb.PersistentClient(path=path)
            # 集合名固定 long_memory（与 exam_0914 保持一致）
            self.chroma_collection = self.chroma_client.get_or_create_collection("long_memory")
        except Exception as e:
            logger.warning(f"ChromaDB 不可用，长期记忆降级为进程内字典：{e}")
            self.chroma_client = None
            self.chroma_collection = None

    # ---------------- 保存 ----------------
    async def save(self, user_id: str, query: str) -> None:
        """写入一条长期事实；空内容直接忽略（不往库里写垃圾）"""
        # 统一转字符串：上游可能传 int 型 user_id
        user_id = str(user_id)
        text = ("" if query is None else str(query)).strip()
        if not text:
            return

        # ---------- 第 1 级：Milvus ----------
        if self.milvus_collection is not None:
            try:
                vector = await self._embed(text)
                data = [{"vector": vector, "user_id": user_id, "text": text}]
                loop = asyncio.get_event_loop()
                # 阻塞的客户端调用丢进线程池，避免卡住事件循环
                await loop.run_in_executor(
                    None,
                    lambda: self.milvus_collection.insert(
                        collection_name=settings.long_memory_collection, data=data
                    ),
                )
                return
            except Exception as e:
                logger.error(f"Milvus 写入长期记忆失败，降级为进程内字典：{e}")

        # ---------- 第 2 级：ChromaDB ----------
        if self.chroma_collection is not None:
            try:
                doc_id = f"memory_{user_id}_{uuid.uuid4().hex}"
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self.chroma_collection.add(
                        ids=[doc_id],
                        documents=[text],
                        # 元数据带 user_id，检索时用 where 过滤，保证跨用户不串记忆（AC-24）
                        metadatas=[{"user_id": user_id}],
                    ),
                )
                return
            except Exception as e:
                logger.error(f"ChromaDB 写入长期记忆失败，降级为进程内字典：{e}")

        # ---------- 第 3 级：内存字典兜底 ----------
        items = _FALLBACK_LONG.setdefault(user_id, [])
        items.append(text)
        if len(items) > _FALLBACK_MAX:
            del items[: len(items) - _FALLBACK_MAX]

    # ---------------- 查询 ----------------
    async def query(self, user_id: str, question: str) -> list:
        """按 user_id 过滤检索 Top3 文本，返回 list[str]（无结果返回 []）"""
        user_id = str(user_id)
        question = "" if question is None else str(question)

        # ---------- 第 1 级：Milvus ----------
        if self.milvus_collection is not None:
            try:
                vector = await self._embed(question)
                loop = asyncio.get_event_loop()
                rs = await loop.run_in_executor(
                    None,
                    lambda: self.milvus_collection.search(
                        collection_name=settings.long_memory_collection,
                        data=[vector],
                        filter=f'user_id == "{user_id}"',
                        limit=3,
                        output_fields=["text"],
                    ),
                )
                # MilvusClient.search 返回 [[{"entity":{"text":...},"distance":...}, ...]]
                texts = []
                for hit in (rs[0] if rs else []):
                    entity = hit.get("entity") if isinstance(hit, dict) else None
                    if entity and entity.get("text"):
                        texts.append(entity["text"])
                return texts[:3]
            except Exception as e:
                logger.error(f"Milvus 检索长期记忆失败，改用下一级：{e}")

        # ---------- 第 2 级：ChromaDB ----------
        if self.chroma_collection is not None:
            try:
                loop = asyncio.get_event_loop()
                rs = await loop.run_in_executor(
                    None,
                    lambda: self.chroma_collection.query(
                        query_texts=[question],
                        n_results=3,
                        where={"user_id": user_id},
                    ),
                )
                docs = (rs or {}).get("documents") or [[]]
                return list(docs[0]) if docs and docs[0] else []
            except Exception as e:
                logger.error(f"ChromaDB 检索长期记忆失败，降级为进程内字典：{e}")

        # ---------- 第 3 级：内存字典（关键词重合度排序） ----------
        return self._fallback_query(user_id, question)

    # ---------------- 内部工具 ----------------
    @staticmethod
    async def _embed(text: str) -> list:
        """调用 Embedding 模型取向量；模型不可用时抛异常，由调用方降级"""
        from app.ai.model.my_model import MyModel

        model = MyModel.get_embedding_model()
        if model is None:
            raise RuntimeError("Embedding 模型不可用")
        loop = asyncio.get_event_loop()
        # embed_query 是同步阻塞的网络调用，同样丢线程池
        return await loop.run_in_executor(None, lambda: model.embed_query(text))

    @staticmethod
    def _fallback_query(user_id: str, question: str) -> list:
        """
        内存降级检索：用最简单的关键词重合度排序。

        打分规则：问题里出现的字符（中文按字、英文按词）在候选文本中命中一次 +1；
        命中数为 0 的候选排后面，最多返回 3 条，保证"总有点上下文可用"。
        """
        items = _FALLBACK_LONG.get(user_id, [])
        if not items:
            return []
        if not question:
            return list(items[-3:])

        # 中文逐字切 + 英文/数字按非字母数字切分，去掉空白与常见单字噪声
        tokens = set()
        for ch in question:
            if "\u4e00" <= ch <= "\u9fff":
                tokens.add(ch)
        for word in "".join(c if c.isalnum() else " " for c in question).split():
            if word.isascii() and word:
                tokens.add(word.lower())

        scored = []
        for index, item in enumerate(items):
            text = item.lower()
            score = sum(1 for token in tokens if token in text)
            scored.append((score, index, item))
        # 先按命中数降序，再按写入先后降序（越新的越优先）
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item for _, _, item in scored[:3]]


if __name__ == "__main__":
    import asyncio

    async def test():
        # 自测：不依赖 Milvus / ChromaDB，三级降级到内存字典也应当可用
        memory = LongMemory()
        await memory.save("u001", "老人有高血压病史，长期服用氨氯地平")
        await memory.save("u001", "对青霉素过敏")
        await memory.save("u001", "儿子在外地工作，每周回来一次")
        # 跨用户隔离验证
        await memory.save("u002", "喜欢听京剧")
        rs = await memory.query("u001", "老人有什么慢性病？对什么药过敏？")
        print("长期记忆检索结果：", rs)
        print("其它用户记忆：", await memory.query("u002", "爱好"))

    asyncio.run(test())
