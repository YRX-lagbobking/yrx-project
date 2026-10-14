"""
健康知识库 RAG 检索工具（契约第 9 节 / 需求 FR-09）

能力清单：
    rag_search(query, top_k=5, disease_type=None) -> list[dict]   # 向量召回 + 元数据过滤 + 精排
    keyword_search(query, top_k=5) -> list[dict]                  # MySQL knowledge_doc 关键词检索
    build_knowledge_base(docs_dir=None) -> int                    # 文档 → 切分 → 向量化 → 入库
    split_text(text, chunk_size=None, overlap=None) -> list[str]  # 语义边界切分
    rag_search_tool（LangChain Tool，供 Agent 调用）

返回结构（契约固定）：
    [{"content":str, "source":str, "version":str, "page":int|str, "score":float}]

降级链路（FR-15 RAG 那一层）：
    向量检索（Milvus） → 关键词检索（MySQL） → 由 app/core/fallback.py 走静态建议
    ⚠️ 边界约定：本模块**只负责前两级**，"向量 → 关键词 → 静态建议"的整体封装
       在 `app/core/fallback.py::rag_search_with_fallback`（由 T4 负责）。
       因此本模块**不导入** app.core.fallback，避免循环导入。

检索链路（混合检索）：
    召回：Milvus 稠密向量（dense）∪ BM25 稀疏检索（sparse）→ RRF 融合
          （由 HYBRID_RETRIEVAL 开关控制；关闭即回到单路向量召回）
    精排：本地 CrossEncoder（RERANK_PROVIDER=local）→ 云端 HTTP rerank → 纯代码混合打分
    降级：以上任意一步不可用 → keyword_search()（MySQL 关键词，最后兜底，保持原样）
"""
import json
import math
import os
import re
from typing import Any

from langchain.tools import tool

from app.ai.tool.schema.rag_schema import RagSchema
from app.core.config import settings
from app.core.logger import logger

# ---------------- 常量 ----------------
# 默认知识文档目录（相对项目根目录）
DEFAULT_DOCS_DIR = "./knowledge_docs"

# 每个文档解析出的元数据字段（与需求 5.9 / 数据库设计 knowledge_doc 对齐）
META_FIELDS = ("source", "version", "disease_type", "page")

# 中文切句标点（优先级从高到低，尽量在语义边界切断）
SENTENCE_ENDINGS = ("。", "！", "？", "；", "\n", ".", "!", "?", ";", "，", ",", " ")

# 静态兜底建议（RAG 全链路失败时由上层使用；这里也导出，方便关键词检索为空的场景）
STATIC_ADVICE = "暂未检索到相关健康知识，请咨询专业医生；若出现胸痛、意识模糊、呼吸困难等紧急症状，请立即拨打 120。"


# ============================================================
# 一、文本切分（纯函数，无任何外部依赖，可直接单测）
# ============================================================
def split_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    """
    按语义边界切分长文本

    参数：
        text       原始文本
        chunk_size 每片最大字符数（默认 settings.chunk_size，512）
        overlap    相邻片段重叠字符数（默认 settings.chunk_overlap，100）
    返回：
        文本片段列表；空文本返回 []

    实现要点：
        1. 先按"句子"切开（优先在 。！？\\n 等标点处断句），
           再贪心地把句子拼进当前片段，尽量不把一句话从中间截断；
        2. 单个句子超过 chunk_size 时，再按标点/硬切兜底；
        3. overlap 从**上一片末尾**截取（按句子边界回退，避免半句话重叠），
           保证跨片语义连续性（需求 5.9：512 字符 + 100 重叠）。
    """
    if not text or not text.strip():
        return []

    chunk_size = int(chunk_size or settings.chunk_size or 512)
    overlap = int(overlap if overlap is not None else (settings.chunk_overlap or 100))
    # 参数保护：overlap 必须小于 chunk_size，否则会死循环
    if chunk_size <= 0:
        chunk_size = 512
    overlap = max(0, min(overlap, chunk_size // 2))

    sentences = _split_sentences(text, chunk_size)

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        # 当前片段装不下这句 → 先把当前片段收尾
        if current and len(current) + len(sentence) > chunk_size:
            chunks.append(current.strip())
            tail = _overlap_tail(current, overlap)     # 取重叠尾巴
            current = tail + sentence if tail else sentence
            # 极端情况：重叠 + 新句仍超长 → 强制切分
            while len(current) > chunk_size:
                chunks.append(current[:chunk_size].strip())
                current = current[chunk_size - overlap:] if overlap else current[chunk_size:]
        else:
            current += sentence

    if current.strip():
        chunks.append(current.strip())

    # 去掉空片与重复片（切分兜底可能产生重复）
    result: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if chunk and (not result or result[-1] != chunk):
            result.append(chunk)
    return result


def _split_sentences(text: str, chunk_size: int) -> list[str]:
    """把文本切成句子；超长句再按标点/长度二次切分"""
    sentences: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if char in SENTENCE_ENDINGS:
            sentences.append(buffer)
            buffer = ""
    if buffer:
        sentences.append(buffer)

    # 二次切分：单句长度超过 chunk_size（例如一段没有标点的长文本）
    result: list[str] = []
    for sentence in sentences:
        if len(sentence) <= chunk_size:
            result.append(sentence)
            continue
        start = 0
        while start < len(sentence):
            result.append(sentence[start:start + chunk_size])
            start += chunk_size
    return result


def _overlap_tail(text: str, overlap: int) -> str:
    """从片段末尾取 overlap 个字符，并尽量回退到最近的句子边界"""
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    # 回退到标点之后，避免以半句话开头
    for index, char in enumerate(tail):
        if char in ("。", "！", "？", "；", "\n", ".", "!", "?", ";"):
            return tail[index + 1:]
    return tail


# ============================================================
# 二、文档解析（md/txt 必需，pdf/docx 可选）
# ============================================================
def _read_text_file(path: str) -> str:
    """读取纯文本文件（md/txt），编码失败时用 replace 兜底"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()


def _read_pdf(path: str) -> list[tuple[int, str]]:
    """
    解析 PDF → [(页码, 文本)]；未安装 pypdf 或解析失败返回 []
    （页码是"溯源"的关键元数据，PQ 需求 5.9 明确要求返回 page）
    """
    try:
        from pypdf import PdfReader                     # 可选依赖：延迟导入
    except ImportError:
        logger.warning(f"未安装 pypdf，跳过 PDF 文档：{path}")
        return []
    except Exception as e:
        logger.error(f"pypdf 导入异常，跳过 PDF：{e}")
        return []

    pages: list[tuple[int, str]] = []
    try:
        reader = PdfReader(path)
        for index, page in enumerate(reader.pages, start=1):
            try:
                pages.append((index, page.extract_text() or ""))
            except Exception as e:
                logger.warning(f"PDF 第 {index} 页解析失败：{e}")
    except Exception as e:
        logger.error(f"PDF 解析失败 {path}：{e}")
    return pages


def _read_docx(path: str) -> str:
    """解析 Word 文档 → 纯文本；未安装 python-docx 或失败返回空串"""
    try:
        from docx import Document                       # 可选依赖：延迟导入
    except ImportError:
        logger.warning(f"未安装 python-docx，跳过 Word 文档：{path}")
        return ""
    except Exception as e:
        logger.error(f"python-docx 导入异常，跳过 Word：{e}")
        return ""
    try:
        document = Document(path)
        return "\n".join(p.text for p in document.paragraphs if p.text)
    except Exception as e:
        logger.error(f"Word 解析失败 {path}：{e}")
        return ""


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """
    解析 Markdown 顶部 YAML front-matter

    形如：
        ---
        source: 中国高血压防治指南
        version: 2024
        disease_type: 高血压
        ---
        正文……
    返回 (元数据 dict, 去掉 front-matter 的正文)
    """
    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            raw_meta, body = parts[1], parts[2]
            for line in raw_meta.strip().splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    return meta, body


def _doc_meta(path: str, text: str) -> dict:
    """生成文档级元数据（source/version/disease_type），front-matter 优先"""
    meta, _ = _parse_front_matter(text)
    name = os.path.basename(path)
    result = {
        "source": meta.get("source") or name,
        "version": meta.get("version") or "v1.0",
        "disease_type": meta.get("disease_type") or _guess_disease_type(name + text[:200]),
    }
    return result


# 常见病种关键词（用于自动推断病种，方便按 disease_type 过滤）
DISEASE_KEYWORDS = {
    "高血压": ("高血压", "血压", "收缩压", "舒张压"),
    "糖尿病": ("糖尿病", "血糖", "胰岛素", "糖化血红蛋白"),
    "冠心病": ("冠心病", "心绞痛", "心肌", "冠脉"),
    "慢阻肺": ("慢阻肺", "COPD", "肺功能", "呼吸困难"),
    "骨质疏松": ("骨质疏松", "补钙", "骨折", "维生素D"),
    "高血脂": ("高血脂", "血脂", "胆固醇", "甘油三酯"),
    "阿尔茨海默病": ("阿尔茨海默", "认知障碍", "记忆力减退", "痴呆"),
}


def _guess_disease_type(text: str) -> str:
    """根据文本内容粗略推断病种；识别不到返回"通用" """
    for disease, keywords in DISEASE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                return disease
    return "通用"


# ============================================================
# 三、向量库（Milvus）读写
# ============================================================
def _get_milvus_collection(create: bool = False, dim: int | None = None) -> Any:
    """
    获取/创建 Milvus 集合（延迟导入 pymilvus）

    返回 None 表示不可用（依赖未安装 / 连不上），调用方走降级。
    """
    try:
        from pymilvus import (                          # 可选依赖：延迟导入
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            connections,
            utility,
        )
    except ImportError:
        logger.warning("未安装 pymilvus，向量检索降级为关键词检索")
        return None
    except Exception as e:
        logger.error(f"pymilvus 导入异常：{e}")
        return None

    try:
        connections.connect(alias="default", host=settings.milvus_host, port=str(settings.milvus_port))
        name = settings.milvus_collection

        if utility.has_collection(name):
            return Collection(name)

        if not create:
            logger.warning(f"Milvus 集合 {name} 不存在（可用 build_knowledge_base 建库）")
            return None

        # 建表：id 主键 + vector 向量 + 元数据字段（source/version/disease_type/page）+ content
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=int(dim or 1536)),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=8192),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="version", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="disease_type", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="page", dtype=DataType.INT64),
        ]
        collection = Collection(name=name, schema=CollectionSchema(fields, description="健康知识库"))
        # HNSW 索引（契约要求：index_type=HNSW、metric_type=COSINE）
        collection.create_index(
            field_name="vector",
            index_params={
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "params": {"M": 16, "efConstruction": 200},
            },
        )
        logger.info(f"Milvus 集合 {name} 创建成功（HNSW / COSINE）")
        return collection
    except Exception as e:
        logger.error(f"Milvus 连接或建表失败，降级为关键词检索：{e}")
        return None


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """
    调用向量模型生成 embedding（延迟导入由 MyModel 内部处理）

    失败返回 []，调用方降级。
    """
    if not texts:
        return []
    try:
        from app.ai.model.my_model import MyModel

        model = MyModel.get_embedding_model()
        if model is None:
            return []
        vectors = model.embed_documents(texts)
        return [list(map(float, v)) for v in vectors]
    except Exception as e:
        logger.error(f"生成向量失败：{e}")
        return []


def _embed_query(query: str) -> list[float]:
    """生成单条查询向量；BGE 中文检索模型给 query 加指令前缀可提升召回"""
    prefix = settings.embedding_query_prefix or ""
    vectors = _embed_texts([prefix + query])
    return vectors[0] if vectors else []

# ============================================================
# 四、关键词检索（MySQL knowledge_doc）
# ============================================================
def _tokenize(text: str) -> list[str]:
    """
    轻量分词（不引入 jieba 等额外依赖）：
        中文按 2-gram 切、英文/数字按单词切，去重后返回，用于关键词重合度打分
    """
    text = (text or "").lower()
    tokens: list[str] = []
    for word in re.findall(r"[a-z0-9_]+", text):
        if len(word) > 1:
            tokens.append(word)
    for block in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(block) == 1:
            tokens.append(block)
        else:
            tokens.extend(block[i:i + 2] for i in range(len(block) - 1))
    # 去重且保持顺序
    seen = set()
    result = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def keyword_search(query: str, top_k: int = 5) -> list[dict]:
    """
    MySQL 关键词检索（向量检索不可用时的降级通道，FR-09 降级第 2 级）

    实现：
        用 `from app.dao.base_dao import BaseDAO` 查询 `knowledge_doc` 表
        （字段：title、source、version、disease_type、page、content），
        参数化查询（%s）杜绝注入；SQL 侧用 LIKE 组合多个关键词缩小范围，
        Python 侧再用 `_hybrid_score` 按关键词重合度打分排序。
    返回：
        [{"content","source","version","page","score"}]；异常返回 []。
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        top_k = max(1, int(top_k))
    except (TypeError, ValueError):
        top_k = 5

    try:
        from app.dao.base_dao import BaseDAO            # DAO 为同步接口（契约第 8 节）
    except Exception as e:
        logger.error(f"导入 BaseDAO 失败，关键词检索不可用：{e}")
        return []

    # 关键词：去掉停用词后取前几个，用 OR LIKE 扩大召回（SQL 侧只做粗筛）
    tokens = [t for t in _tokenize(query) if len(t) >= 2][:5] or [query[:20]]
    where_parts = " OR ".join(["content LIKE %s", "title LIKE %s"] * len(tokens))
    sql = (
        f"SELECT doc_id, title, source, version, disease_type, page, content "
        f"FROM knowledge_doc WHERE {where_parts} LIMIT 50"
    )
    args: list = []
    for token in tokens:
        like = f"%{token}%"
        args.extend([like, like])

    try:
        rows = BaseDAO.query(sql, tuple(args))
    except Exception as e:
        logger.error(f"关键词检索查询失败：{e}")
        return []

    results: list[dict] = []
    for row in rows or []:
        try:
            # BaseDAO.query 返回 list[tuple]；为兼容 DictCursor 也支持 dict
            if isinstance(row, dict):
                doc_id = row.get("doc_id")
                title = row.get("title")
                source = row.get("source")
                version = row.get("version")
                disease_type = row.get("disease_type")
                page = row.get("page")
                content = row.get("content")
            else:
                doc_id, title, source, version, disease_type, page, content = (list(row) + [None] * 7)[:7]
            content = content or title or ""
            score = _hybrid_score(query, content, None)
            results.append({
                "content": content,
                "source": source or "本地知识库",
                "version": version or "v1.0",
                "page": page if page is not None else "",
                "score": score,
                "disease_type": disease_type or "通用",
                "doc_id": doc_id,
            })
        except Exception as e:
            logger.warning(f"关键词检索结果行解析失败：{e}")
            continue

    results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return results[:top_k]


# ============================================================
# 五、混合打分与精排
# ============================================================
def _hybrid_score(query: str, content: str, vector_score: float | None) -> float:
    """
    纯代码混合打分：关键词重合度 + 向量相似度

    公式：
        0.6 * 关键词重合度（query 的 2-gram 在 content 中的覆盖率）
      + 0.4 * 向量相似度（没有向量分数时，该部分按关键词分数补齐，避免整体被拉低）
    返回 0~1 的浮点数（保留 4 位小数）。
    """
    query_tokens = _tokenize(query)
    content_lower = (content or "").lower()
    if query_tokens:
        hit = sum(1 for token in query_tokens if token in content_lower)
        keyword_score = hit / len(query_tokens)
    else:
        keyword_score = 0.0

    if vector_score is None:
        score = keyword_score
    else:
        # Milvus COSINE 相似度范围 [-1,1] → 归一到 [0,1]
        normalized = max(0.0, min(1.0, (float(vector_score) + 1.0) / 2.0))
        score = 0.6 * keyword_score + 0.4 * normalized
    return round(score, 4)

# 本地 CrossEncoder 精排模型（单例懒加载：首次用才加载，避免启动即占 1G 内存）
_cross_encoder = None


def _get_cross_encoder():
    """加载本地精排模型（sentence_transformers.CrossEncoder）；失败返回 None"""
    global _cross_encoder
    if _cross_encoder is None:
        try:
            path = getattr(settings, "rerank_model_path", "") or ""
            if not path:
                return None
            from sentence_transformers import CrossEncoder   # 延迟导入

            _cross_encoder = CrossEncoder(path, device=getattr(settings, "rerank_device", "cpu"))
            logger.info(f"本地精排模型加载完成：{path}")
        except Exception as e:
            logger.warning(f"本地精排模型不可用，将跳过：{e}")
            _cross_encoder = None
    return _cross_encoder
def _rerank(query: str, candidates: list[dict], top_k: int) -> list[dict]:
    """
    精排（FR-09 检索链路的最后一环）

    策略：
        1. 先做**纯代码**混合打分（关键词重合度 + 向量分数），一定可用；
        2. 若配置了 DASHSCOPE_API_KEY，再尝试调用 DashScope 兼容的 rerank HTTP 接口；
        3. 任何异常（未装 requests / 网络失败 / 返回格式变化）都直接退回第 1 步的排序。
    """
    if not candidates:
        return []

    # ---- 1) 纯代码混合打分 ----
    for item in candidates:
        item["score"] = _hybrid_score(query, item.get("content", ""), item.get("_vector_score"))
    ranked = sorted(candidates, key=lambda x: x.get("score", 0.0), reverse=True)

    # ---- 2) 本地 CrossEncoder 精排（优先级最高，最准）----
    if (getattr(settings, "rerank_provider", "code") or "").lower() in ("local", "huggingface", "cross_encoder"):
        try:
            encoder = _get_cross_encoder()
            if encoder is not None:
                pairs = [[query, item.get("content", "")] for item in candidates]
                raw_scores = encoder.predict(pairs)          # 返回 logits
                for item, raw in zip(candidates, raw_scores):
                    item["_rerank_raw"] = float(raw)
                    # logits → 0~1 相关概率，便于前端展示与比较
                    item["score"] = round(1.0 / (1.0 + math.exp(-float(raw))), 4)
                candidates = sorted(candidates, key=lambda x: x["_rerank_raw"], reverse=True)
                logger.info(f"本地 CrossEncoder 精排完成，候选 {len(candidates)} 条")
                return candidates[:top_k]
        except Exception as e:
            logger.warning(f"本地精排失败，退回代码混合打分：{e}")

    # ---- 3) 可选：HTTP rerank（云端；本地精排已在上面执行）----
    try:
        if not settings.dashscope_api_key or not settings.rerank_model:
            return ranked[:top_k]
        import requests                                  # 可选依赖：延迟导入

        payload = {
            "model": settings.rerank_model,
            "input": {"query": query, "documents": [c.get("content", "")[:2000] for c in ranked]},
            "parameters": {"top_n": min(top_k, len(ranked))},
        }
        response = requests.post(
            f"{settings.openai_api_base.rstrip('/')}/rerank",
            headers={
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=8,
        )
        if response.status_code != 200:
            logger.warning(f"rerank 接口返回 {response.status_code}，使用代码混合打分结果")
            return ranked[:top_k]

        body = response.json()
        items = (body.get("output") or {}).get("results") or body.get("results") or []
        if not items:
            return ranked[:top_k]

        reranked: list[dict] = []
        for entry in items:
            index = entry.get("index")
            if not isinstance(index, int) or index < 0 or index >= len(ranked):
                continue
            item = dict(ranked[index])
            # rerank 分数归一到 0~1（不同服务返回量纲不一致，这里只保证可比）
            raw = entry.get("relevance_score", entry.get("score", item.get("score", 0.0)))
            try:
                item["score"] = round(1.0 / (1.0 + math.exp(-float(raw))), 4)  # sigmoid 归一
            except Exception:
                pass
            reranked.append(item)
        if reranked:
            logger.info(f"rerank 精排完成，返回 {len(reranked[:top_k])} 条")
            return reranked[:top_k]
    except ImportError:
        pass                                             # 未安装 requests：静默使用代码打分
    except Exception as e:
        logger.warning(f"rerank 精排失败，退回代码混合打分结果：{e}")
    return ranked[:top_k]



def _fuse_dense_and_bm25(query: str, dense_candidates: list[dict], top_k: int,
                         disease_type: str | None = None) -> list[dict]:
    """
    混合召回融合：dense(Milvus 向量) ∪ sparse(BM25) → RRF（FR-09 检索链路升级）

    为什么要双路：
        单路向量召回会漏掉"字面命中但语义不相似"的片段（药品名、缩写、精确数值、
        分条列举的注意事项），BM25 恰好擅长字面匹配；两路独立召回后融合，互为补充。

    为什么用 RRF 而不是线性加权：
        向量分是 COSINE（0~1 有界），BM25 分是无界且随语料规模变化的量纲，
        线性加权需反复调参且不稳定；RRF 只用"排名"：
            score(d) = Σ_i 1 / (k + rank_i(d))，k 取 60
        免调参、天然可比，是混合检索的标准做法。

    开关与降级：
        `HYBRID_RETRIEVAL=false` → 原样返回 dense_candidates（等于升级前的单路行为）；
        BM25 模块不可用 / 无命中 / 任何异常 → 同样原样返回，绝不影响主链路。
    """
    if not getattr(settings, "hybrid_retrieval", True):
        return dense_candidates

    try:
        from app.ai.tool.bm25_util import bm25_search
    except Exception as e:
        logger.warning(f"BM25 模块不可用，退化为单路向量召回：{e}")
        return dense_candidates

    # 1) 第二路召回：BM25 稀疏检索
    sparse_hits = bm25_search(query, top_k=max(top_k * 3, top_k))
    if not sparse_hits:
        logger.info("BM25 无命中，保持单路向量召回")
        return dense_candidates

    # 2) RRF 融合（Reciprocal Rank Fusion）
    k = float(getattr(settings, "rrf_k", 60) or 60)
    fused: dict = {}

    def _doc_key(item: dict) -> str:
        content = (item.get("content") or "").strip()
        return content[:120] if content else str(item.get("doc_id") or "")

    for rank, item in enumerate(dense_candidates, start=1):
        key = _doc_key(item)
        if not key:
            continue
        entry = fused.setdefault(
            key, {"item": item, "rrf": 0.0, "from_dense": False, "from_sparse": False}
        )
        entry["rrf"] += 1.0 / (k + rank)
        entry["from_dense"] = True

    for rank, item in enumerate(sparse_hits, start=1):
        key = _doc_key(item)
        if not key:
            continue
        entry = fused.setdefault(
            key, {"item": item, "rrf": 0.0, "from_dense": False, "from_sparse": False}
        )
        entry["rrf"] += 1.0 / (k + rank)
        entry["from_sparse"] = True
        # 只被 BM25 召回的片段：补记 BM25 分数（_hybrid_score 会退化为关键词分）
        entry["item"].setdefault("bm25_score", item.get("score"))

    # 3) 按 RRF 倒序 + 病种二次过滤（与向量路保持一致）
    merged = sorted(fused.values(), key=lambda x: x["rrf"], reverse=True)
    result: list[dict] = []
    for entry in merged:
        item = entry["item"]
        if disease_type and item.get("disease_type") and item.get("disease_type") != disease_type:
            continue
        item["_rrf"] = round(entry["rrf"], 6)
        item["_recall"] = (
            "dense+sparse" if entry["from_dense"] and entry["from_sparse"]
            else ("dense" if entry["from_dense"] else "sparse")
        )
        result.append(item)

    # 4) 控制候选规模（精排模型对每条候选都要算一次，别无限放大）
    result = result[: max(top_k * 4, top_k)]
    logger.info(
        f"混合召回完成：dense {len(dense_candidates)} + BM25 {len(sparse_hits)} "
        f"→ 融合 {len(result)} 条候选（RRF k={k:g}）"
    )
    return result


# ============================================================
# 六、向量检索主入口
# ============================================================
def rag_search(query: str, top_k: int = 5, disease_type: str | None = None) -> list[dict]:
    """
    知识检索主入口：向量召回 → 元数据过滤 → 精排

    参数：
        query        用户问题
        top_k        返回条数
        disease_type 病种过滤（Milvus 标量过滤 + 代码二次过滤）
    返回：
        [{"content","source","version","page","score"}]

    降级：
        `settings.rag_mode == "keyword"` → 直接关键词检索；
        Milvus/Embedding 不可用 → 自动退回关键词检索（不抛异常）。
        整体再不可用由上层 `app/core/fallback.py` 输出静态建议。
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        top_k = max(1, int(top_k))
    except (TypeError, ValueError):
        top_k = 5

    # 1) 显式关键词模式（无向量库的轻量模式，R-04 对策）
    if (settings.rag_mode or "").lower() == "keyword":
        logger.info("RAG_MODE=keyword，直接走关键词检索")
        return keyword_search(query, top_k)

    # 2) 向量检索
    collection = _get_milvus_collection(create=False)
    if collection is None:
        logger.warning("向量库不可用，rag_search 降级为关键词检索")
        return keyword_search(query, top_k)

    try:
        vector = _embed_query(query)
        if not vector:
            logger.warning("查询向量生成失败，rag_search 降级为关键词检索")
            return keyword_search(query, top_k)

        collection.load()
        expr = f'disease_type == "{disease_type}"' if disease_type else None
        search_result = collection.search(
            data=[vector],
            anns_field="vector",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=max(top_k * 3, top_k),                  # 多召回一些，交给精排
            expr=expr,
            output_fields=["content", "source", "version", "disease_type", "page"],
        )

        candidates: list[dict] = []
        for hits in search_result or []:
            for hit in hits:
                entity = getattr(hit, "entity", None)
                getter = getattr(entity, "get", None)
                item = {
                    "content": getter("content") if callable(getter) else "",
                    "source": getter("source") if callable(getter) else "健康知识库",
                    "version": getter("version") if callable(getter) else "v1.0",
                    "page": getter("page") if callable(getter) else "",
                    "disease_type": getter("disease_type") if callable(getter) else "",
                    "_vector_score": float(getattr(hit, "distance", 0.0) or 0.0),
                }
                # 代码侧再过滤一次病种，防止 Milvus 表达式不生效
                if disease_type and item["disease_type"] and item["disease_type"] != disease_type:
                    continue
                candidates.append(item)

        # ---- 混合召回：dense(Milvus) ∪ sparse(BM25) → RRF 融合 ----
        try:
            candidates = _fuse_dense_and_bm25(query, candidates, top_k, disease_type)
        except Exception as e:
            logger.warning(f"混合召回融合失败，退化为单路向量召回：{e}")

        if not candidates:
            logger.warning("向量检索与 BM25 均无命中，降级为关键词检索")
            return keyword_search(query, top_k)

        results = _rerank(query, candidates, top_k)
        # 清理内部字段，返回契约规定的 5 个键
        return [
            {
                "content": r.get("content", ""),
                "source": r.get("source", "健康知识库"),
                "version": r.get("version", "v1.0"),
                "page": r.get("page", ""),
                "score": float(r.get("score", 0.0)),
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f"向量检索失败，降级为关键词检索：{e}")
        return keyword_search(query, top_k)


# ============================================================
# 七、知识库构建
# ============================================================
def build_knowledge_base(docs_dir: str | None = None) -> int:
    """
    构建知识库：扫描文档 → 解析 → 切分 → 向量化 → 写入 Milvus

    支持格式（按依赖可用性自动取舍）：
        .md / .txt  必需（标准库即可解析）
        .pdf        可选，需 pypdf
        .docx       可选，需 python-docx
    参数：
        docs_dir 文档目录，默认 ./knowledge_docs
    返回：
        成功入库的片段条数（失败返回 0，不抛异常）
    """
    docs_dir = docs_dir or DEFAULT_DOCS_DIR
    if not os.path.isdir(docs_dir):
        logger.error(f"知识文档目录不存在：{docs_dir}")
        return 0

    # 1) 收集文档
    files: list[str] = []
    for root, _dirs, names in os.walk(docs_dir):
        for name in names:
            if name.lower().endswith((".md", ".txt", ".pdf", ".docx")):
                files.append(os.path.join(root, name))
    if not files:
        logger.warning(f"目录 {docs_dir} 下没有可解析的文档")
        return 0

    # 2) 解析 + 切分，得到待入库清单
    pending: list[dict] = []
    for path in files:
        try:
            lower = path.lower()
            if lower.endswith(".pdf"):
                # PDF 按页解析，页码即溯源信息
                for page_no, page_text in _read_pdf(path):
                    meta, _ = _parse_front_matter(page_text)
                    doc_meta = _doc_meta(path, page_text)
                    for chunk in split_text(page_text):
                        pending.append({
                            "content": chunk,
                            "source": meta.get("source") or doc_meta["source"],
                            "version": meta.get("version") or doc_meta["version"],
                            "disease_type": meta.get("disease_type") or doc_meta["disease_type"],
                            "page": page_no,
                        })
                continue

            if lower.endswith(".docx"):
                text = _read_docx(path)
            else:
                text = _read_text_file(path)

            meta, body = _parse_front_matter(text)
            doc_meta = _doc_meta(path, text)
            for index, chunk in enumerate(split_text(body), start=1):
                pending.append({
                    "content": chunk,
                    "source": meta.get("source") or doc_meta["source"],
                    "version": meta.get("version") or doc_meta["version"],
                    "disease_type": meta.get("disease_type") or doc_meta["disease_type"],
                    "page": index,                        # 纯文本/Word 用片段序号代替页码
                })
        except Exception as e:
            logger.error(f"解析文档失败，跳过 {path}：{e}")
            continue

    if not pending:
        logger.warning("没有解析出任何可入库的知识片段")
        return 0

    # 3) 向量化
    vectors: list[list[float]] = []
    batch_size = 16                                   # 分批向量化，避免单次请求过大
    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        batch_vectors = _embed_texts([item["content"] for item in batch])
        if len(batch_vectors) != len(batch):
            logger.error("向量生成失败或数量不匹配，知识库构建中止")
            return 0
        vectors.extend(batch_vectors)

    # 4) 建表 + 入库
    collection = _get_milvus_collection(create=True, dim=len(vectors[0]))
    if collection is None:
        logger.error("Milvus 不可用，知识库构建失败（可改用 RAG_MODE=keyword）")
        return 0

    try:
        # ⚠️ pymilvus 2.6 语义（踩过的坑，别改回 list[list]）：
        #   Collection.insert 只把 dict / list[dict] 当"行式"；
        #   传 list[list] 会被当成"列式"，从而要求"列数 == 非自增主键字段数（这里是 6）"，
        #   否则报 DataNotMatchException: The data doesn't match with schema fields, expect 6 list, got N。
        #   因此这里用官方 docstring 推荐的**列式**写法：每列一个列表，
        #   顺序与 schema 中除自增主键外的字段顺序严格一致：
        #   vector → content → source → version → disease_type → page
        rows = [
            vectors,                                        # 列 1：全部向量（768 维）
            [item["content"] for item in pending],          # 列 2
            [item["source"] for item in pending],           # 列 3
            [item["version"] for item in pending],          # 列 4
            [item["disease_type"] for item in pending],     # 列 5
            [int(item["page"] or 0) for item in pending],   # 列 6
        ]
        collection.insert(rows)
        collection.flush()
        # ⚠️ 列式插入时 rows 是"列"的列表，len(rows) 是列数(6)而不是入库条数，
        #    条数必须以 pending（待入库片段）为准，否则日志和返回值都会少报。
        logger.info(f"知识库构建完成：{len(pending)} 条片段入库（集合 {settings.milvus_collection}）")
        return len(pending)
    except Exception as e:
        logger.error(f"写入 Milvus 失败：{e}")
        return 0


# ============================================================
# 八、LangChain Tool 出口
# ============================================================
@tool(args_schema=RagSchema)
def rag_search_tool(query: str, top_k: int = 5, disease_type: str | None = None) -> str:
    """
    检索健康知识库（高血压、糖尿病等慢病管理、用药常识、老年照护建议）

    返回带溯源信息的知识片段文本（来源 / 版本 / 页码 / 相关度），
    用于让回答"有依据、可追溯"（FR-09 溯源）。
    """
    results = rag_search(query, top_k, disease_type)
    if not results:
        return STATIC_ADVICE
    lines = []
    for index, item in enumerate(results, start=1):
        lines.append(
            f"[{index}] {item.get('content', '')}\n"
            f"    来源：{item.get('source', '未知')} 版本：{item.get('version', '-')} "
            f"页码：{item.get('page', '-')} 相关度：{item.get('score', 0)}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    # # 自测：无 Milvus / 无 MySQL / 无向量模型时应优雅降级，不抛异常
    # demo_text = (
    #     "高血压老人应低盐饮食。每日食盐摄入量建议不超过 5 克。"
    #     "同时要规律监测血压，早晨起床后测量更准确。\n"
    #     "如果出现头晕、胸闷等症状，应及时就医。收缩压持续高于 180 需要立即联系医生！"
    # )
    # chunks = split_text(demo_text, chunk_size=60, overlap=20)
    # print(f"切分结果：{len(chunks)} 片")
    # for i, c in enumerate(chunks, 1):
    #     print(f"  第{i}片({len(c)}字)：{c}")
    # print("空文本切分：", split_text(""))
    # print("rag_mode =", settings.rag_mode)
    # print("向量检索（会降级）：", rag_search("高血压饮食注意什么", top_k=2))
    # print("关键词检索（MySQL 不可用时应为 []）：", keyword_search("高血压", top_k=2))
    # print("知识库构建（目录不存在时应为 0）：", build_knowledge_base("./not_exist_docs"))
    # print("工具出口：", rag_search_tool.invoke({"query": "高血压注意什么", "top_k": 2})[:80])
    count = build_knowledge_base(r"E:\daimaxiangmu\workspace2\agent_project\knowledge_docs")
    print(f"入库片段数：{count}")
    if count == 0:
        print("⚠️ 入库失败或没有可入库片段 —— 请看上面的 ERROR 日志")
        print("   常见原因：路径不对 / embedding 模型加载失败 / Milvus 未启动 / 目录里没有 .md 文件")
    else:
        print("✅ 入库成功，可用 `python -m app.test.test_hybrid` 验证检索")