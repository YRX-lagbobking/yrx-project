"""
BM25 稀疏检索（混合检索的"关键词一路"）

定位（重要）：
    本模块是**与 Milvus 稠密向量检索并行**的第二路召回，两路在
    `rag_tool.rag_search()` 里用 RRF 融合，形成真正的混合检索。
    它**不是** `keyword_search()` —— 那个是 MySQL LIKE 的最后一级降级通道，
    按需求保持不变（降级方案不引入新依赖）。

特性：
    1. 语料双源合并，保证"两路召回看的是同一批文档"：
       ─ knowledge_docs/ 目录（与 Milvus 向量库同源，复用 rag_tool 的解析/切分管线）
       ─ MySQL knowledge_doc 表（与降级通道同源）
       按内容去重后合并。
    2. 分词优先 jieba（未安装则退化为中文 2-gram，效果略降但不报错）；
    3. 打分优先 rank_bm25.BM25Okapi（未安装则用内置纯 Python 的 BM25Okapi 实现，
       公式与 rank_bm25 一致），保证**零新依赖也能跑**；
    4. 进程级 TTL 缓存，避免每次问询都重建索引。

用法：
    from app.ai.tool.bm25_util import bm25_search
    hits = bm25_search("老人血压高平时要注意什么", top_k=5)
"""
import hashlib
import math
import os
import time
from collections import Counter

from app.core.config import settings
from app.core.logger import logger

# ---------------------------------------------------------------------------
# 停用词表（沿用既往项目的 BM25Util）
# ---------------------------------------------------------------------------
STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "他", "她", "它", "们", "这个", "那个", "什么", "哪", "怎么",
    "吗", "呢", "吧", "啊", "哦", "还", "被", "把", "让", "从", "对", "与", "但",
    "而", "或", "所", "为", "以", "及", "可", "可以", "能", "能够", "应该", "需要",
    "已经", "虽然", "如果", "因为", "所以", "只是", "还是", "不过", "然后", "之",
    "其", "中", "等", "等等", "即", "使", "向", "将", "按", "当", "于", "由", "比",
    "除了", "关于", "以及", "并且", "此外", "另外", "过", "来", "做", "作", "像",
    "如", "如同", "由于",
}

# 进程级缓存：docs（语料）+ bm25（索引）+ built_at（构建时间）
_CACHE = {"docs": [], "bm25": None, "built_at": 0.0, "engine": ""}


# ---------------------------------------------------------------------------
# 一、分词
# ---------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    """
    中文分词：优先 jieba；未安装时退化为 2-gram（中文场景够用，英文按单词切）
    """
    text = text or ""
    try:
        import jieba  # 可选依赖：延迟导入

        tokens = [t.strip() for t in jieba.cut(text)]
        return [t for t in tokens if t and t not in STOP_WORDS]
    except ImportError:
        pass

    tokens: list[str] = []
    import re

    for word in re.findall(r"[a-z0-9_]+", text.lower()):
        if len(word) > 1:
            tokens.append(word)
    for block in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(block) == 1:
            tokens.append(block)
        else:
            tokens.extend(block[i:i + 2] for i in range(len(block) - 1))
    return [t for t in tokens if t not in STOP_WORDS]


# ---------------------------------------------------------------------------
# 二、BM25 打分（内置纯 Python 实现，公式与 rank_bm25.BM25Okapi 一致）
# ---------------------------------------------------------------------------
class _PureBM25:
    """
    BM25Okapi 的纯标准库实现（k1=1.5, b=0.75）

    idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
    score(q,d) = Σ idf(t) * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * |d| / avgdl))
    """

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_len = [len(doc) for doc in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.freqs = [Counter(doc) for doc in corpus_tokens]
        df: Counter = Counter()
        for doc in corpus_tokens:
            for term in set(doc):
                df[term] += 1
        n = len(corpus_tokens)
        self.idf = {term: math.log(1 + (n - cnt + 0.5) / (cnt + 0.5)) for term, cnt in df.items()}

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * len(self.freqs)
        for term in query_tokens:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, freq in enumerate(self.freqs):
                tf = freq.get(term, 0)
                if not tf:
                    continue
                norm = self.k1 * (1 - self.b + self.b * (self.doc_len[i] / (self.avgdl or 1.0)))
                scores[i] += idf * (tf * (self.k1 + 1)) / (tf + norm)
        return scores


# ---------------------------------------------------------------------------
# 三、语料装载（knowledge_docs/ ∪ MySQL knowledge_doc）
# ---------------------------------------------------------------------------
def _content_key(content: str) -> str:
    """语料去重键：内容前 200 字符的 md5"""
    return hashlib.md5((content or "")[:200].encode("utf-8", errors="ignore")).hexdigest()


def _load_from_docs_dir(docs_dir: str | None = None) -> list[dict]:
    """从 knowledge_docs/ 解析语料（复用 rag_tool 的解析与切分管线，与向量库同源）"""
    docs_dir = docs_dir or getattr(settings, "bm25_docs_dir", "") or "./knowledge_docs"
    if not os.path.isdir(docs_dir):
        logger.warning(f"BM25 文档目录不存在，跳过：{docs_dir}")
        return []
    try:
        from app.ai.tool import rag_tool as rt   # 函数内导入，避免与 rag_tool 循环导入
    except Exception as e:
        logger.warning(f"复用 rag_tool 解析管线失败，跳过目录语料：{e}")
        return []

    result: list[dict] = []
    for root, _dirs, names in os.walk(docs_dir):
        for name in names:
            lower = name.lower()
            if not lower.endswith((".md", ".txt", ".docx")):
                continue
            path = os.path.join(root, name)
            try:
                if lower.endswith(".docx"):
                    text = rt._read_docx(path)
                else:
                    text = rt._read_text_file(path)
                meta, body = rt._parse_front_matter(text)
                doc_meta = rt._doc_meta(path, text)
                for index, chunk in enumerate(rt.split_text(body), start=1):
                    result.append({
                        "content": chunk,
                        "source": meta.get("source") or doc_meta["source"],
                        "version": meta.get("version") or doc_meta["version"],
                        "disease_type": meta.get("disease_type") or doc_meta["disease_type"],
                        "page": index,
                    })
            except Exception as e:
                logger.error(f"BM25 解析文档失败，跳过 {path}：{e}")
    logger.info(f"BM25 目录语料：{len(result)} 条（来自 {docs_dir}）")
    return result


def _load_from_mysql() -> list[dict]:
    """从 MySQL knowledge_doc 拉语料（与降级通道同源）"""
    try:
        from app.dao.base_dao import BaseDAO
    except Exception as e:
        logger.warning(f"BM25 MySQL 语料不可用：{e}")
        return []
    try:
        rows = BaseDAO.query(
            "SELECT doc_id, title, source, version, disease_type, page, content FROM knowledge_doc"
        ) or []
    except Exception as e:
        logger.warning(f"BM25 MySQL 语料查询失败：{e}")
        return []

    result: list[dict] = []
    for row in rows:
        try:
            doc_id, title, source, version, dtype, page, content = (list(row) + [None] * 7)[:7]
            result.append({
                "doc_id": doc_id,
                "content": content or title or "",
                "source": source or "本地知识库",
                "version": version or "v1.0",
                "disease_type": dtype or "通用",
                "page": page if page is not None else "",
            })
        except Exception:
            continue
    logger.info(f"BM25 MySQL 语料：{len(result)} 条")
    return result


def get_corpus(force: bool = False) -> list[dict]:
    """
    合并双源语料并按内容去重（带 TTL 缓存）

    TTL 由 `BM25_CACHE_TTL`（默认 600 秒）控制：知识库更新后最多等这么久生效。
    """
    ttl = int(getattr(settings, "bm25_cache_ttl", 600) or 600)
    if (not force) and _CACHE["docs"] and (time.time() - _CACHE["built_at"]) < ttl:
        return _CACHE["docs"]

    merged: dict = {}
    for item in _load_from_docs_dir() + _load_from_mysql():
        key = _content_key(item.get("content", ""))
        if key and key not in merged:
            merged[key] = item
    docs = list(merged.values())
    # 语料变了 → 索引必须重建
    _CACHE.update(docs=docs, bm25=None, built_at=time.time())
    logger.info(f"BM25 语料就绪：{len(docs)} 条（目录 ∪ MySQL，已按内容去重）")
    return docs


# ---------------------------------------------------------------------------
# 四、索引构建与检索
# ---------------------------------------------------------------------------
def get_index():
    """构建/复用 BM25 索引（进程级单例）；返回 (index, docs, engine)"""
    docs = get_corpus()
    if not docs:
        return None, [], ""
    if _CACHE["bm25"] is None:
        tokenized = [tokenize(d.get("content", "")) for d in docs]
        try:
            from rank_bm25 import BM25Okapi    # 可选依赖：装了就用它（与既往项目一致）

            _CACHE["bm25"] = BM25Okapi(tokenized)
            _CACHE["engine"] = "rank_bm25"
        except ImportError:
            _CACHE["bm25"] = _PureBM25(tokenized)
            _CACHE["engine"] = "builtin"
        logger.info(f"BM25 索引构建完成：{len(docs)} 条语料，引擎={_CACHE['engine']}")
    return _CACHE["bm25"], _CACHE["docs"], _CACHE["engine"]


def bm25_search(query: str, top_k: int = 5) -> list[dict]:
    """
    BM25 检索，返回结构与 `rag_search` 一致（便于 RRF 融合）

    返回：[{"content","source","version","page","disease_type","score"}]
    无命中或异常返回 []（绝不抛异常，保证 rag_search 总能继续走别的路）。
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        top_k = max(1, int(top_k))
    except (TypeError, ValueError):
        top_k = 5

    try:
        index, docs, _engine = get_index()
        if index is None or not docs:
            return []
        scores = index.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [
            {**docs[i], "score": round(float(scores[i]), 6)}
            for i in order
            if float(scores[i]) > 0
        ]
    except Exception as e:
        logger.warning(f"BM25 检索失败，跳过稀疏召回：{e}")
        return []


def reset_cache() -> None:
    """清空缓存（知识库更新后可手动调用，或等 TTL 自动失效）"""
    _CACHE.update(docs=[], bm25=None, built_at=0.0, engine="")


if __name__ == "__main__":
    # 自测：不依赖 MySQL / Milvus，用内存语料验证 BM25 能正确排序
    demo_docs = [
        {"content": "高血压患者应低盐饮食，每日食盐不超过 5 克。", "source": "高血压指南", "page": 1},
        {"content": "糖尿病低血糖时立即进食 15 克快速升糖食物。", "source": "糖尿病指南", "page": 1},
        {"content": "老年人用药要按时按量，漏服不可加倍补服。", "source": "用药安全", "page": 1},
    ]
    index = _PureBM25([tokenize(d["content"]) for d in demo_docs])
    scores = index.get_scores(tokenize("高血压 饮食"))
    rank = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    print("内置 BM25 排序：", [(demo_docs[i]["source"], round(scores[i], 4)) for i in rank])
    print("分词示例：", tokenize("高血压患者应低盐饮食"))
    print("缓存引擎：", _CACHE["engine"] or "（未构建，需数据库）")
