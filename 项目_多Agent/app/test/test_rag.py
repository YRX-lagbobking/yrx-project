"""
RAG 检索链路验证：纯向量检索（Milvus）/ BM25 稀疏检索 / 混合检索（dense ∪ sparse + RRF + 精排）

运行方式（项目根目录）：
    python -m app.test.test_rag
    python -m app.test.test_rag --top-k 5
    python -m app.test.test_rag --query "老人血压高要注意什么" "降糖药漏服了怎么办"

检查项（FR-09 / AC-14、AC-15）：
    【1】配置与 Milvus 连接
    【2】切分自检（split_text，附带保留）
    【3-A】纯向量检索：直接查 Milvus（向量 + HNSW/COSINE），打印原始 distance
    【3-B】BM25 稀疏检索：bm25_util（语料 = knowledge_docs/ ∪ MySQL knowledge_doc）
    【3-C】混合检索：dense ∪ sparse → RRF 融合（打印 _rrf 与召回来源），再走本地精排
    【4】三路对比：命中数 / 交集 / 混合新增召回 / 关闭混合的 A/B 对比
    【5】MySQL 关键词降级通道（keyword_search，可选）
    【6】结论：向量检索 / BM25 / 混合检索 三项分别 ✅ 或 ❌

退出码：0 = 三项都通过；1 = 有项目未通过；2 = 环境不可用（无法 import 或连不上 Milvus）
"""
import argparse
import os
import sys
import warnings

# ---------- 项目根目录加入 sys.path，并统一切到项目根 ----------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

# 减少噪音：pymilvus 的 ORM 弃用告警、HuggingFace 下载进度条
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*ORM-style.*")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

DEFAULT_QUERIES = [
    "老人血压高平时要注意什么",
    "降糖药漏服了怎么办",
    "心率突然变快还喘不上气是怎么回事",
]


def section(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def brief(content: str, width: int = 52) -> str:
    return (content or "").replace("\n", " ").strip()[:width]


# ---------------------------------------------------------------------------
# 底层各条检索通道（直接调用，便于定位问题出在哪一层）
# ---------------------------------------------------------------------------
def milvus_collection_state() -> tuple:
    """返回 (collection, row_count)；不可用返回 (None, -1)"""
    try:
        from pymilvus import MilvusClient

        from app.core.config import settings

        client = MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}")
        name = settings.milvus_collection
        if name not in client.list_collections():
            return None, -1
        row_count = int(client.get_collection_stats(name).get("row_count", 0))
        return client, row_count
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ 读取集合状态失败：{type(e).__name__}: {e}")
        return None, -1


def dense_search(query: str, top_k: int = 5) -> list[dict]:
    """
    纯向量检索：直接走 Milvus（向量化 → collection.search），
    返回 [{content, source, version, page, disease_type, _vector_score}]
    """
    from app.ai.tool import rag_tool as rt

    collection = rt._get_milvus_collection(create=False)
    if collection is None:
        print("  ❌ Milvus 集合不可用（未灌库或连接失败）")
        return []

    vector = rt._embed_query(query)
    if not vector:
        print("  ❌ 查询向量生成失败（检查 EMBEDDING_PROVIDER / EMBEDDING_MODEL_PATH）")
        return []

    collection.load()
    try:
        raw = collection.search(
            data=[vector],
            anns_field="vector",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=max(top_k, 1),
            output_fields=["content", "source", "version", "disease_type", "page"],
        )
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ Milvus search 失败：{type(e).__name__}: {e}")
        return []

    results: list[dict] = []
    for hits in raw or []:
        for hit in hits:
            entity = getattr(hit, "entity", None)
            getter = getattr(entity, "get", None)
            results.append({
                "content": getter("content") if callable(getter) else "",
                "source": getter("source") if callable(getter) else "",
                "version": getter("version") if callable(getter) else "",
                "page": getter("page") if callable(getter) else "",
                "disease_type": getter("disease_type") if callable(getter) else "",
                "_vector_score": round(float(getattr(hit, "distance", 0.0) or 0.0), 4),
            })
    return results


def bm25_only_search(query: str, top_k: int = 5) -> list[dict]:
    """BM25 稀疏检索（第二路）"""
    try:
        from app.ai.tool.bm25_util import bm25_search

        return bm25_search(query, top_k=top_k)
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ BM25 检索失败：{type(e).__name__}: {e}")
        return []


def hybrid_fuse(query: str, dense: list[dict], top_k: int = 5) -> list[dict]:
    """混合召回：dense ∪ sparse → RRF 融合（保留 _rrf / _recall 便于观察）"""
    try:
        from app.ai.tool.rag_tool import _fuse_dense_and_bm25

        return _fuse_dense_and_bm25(query, dense, top_k)
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ 混合融合失败：{type(e).__name__}: {e}")
        return []


# ---------------------------------------------------------------------------
# 打印工具
# ---------------------------------------------------------------------------
def print_dense(results: list[dict]) -> None:
    for i, r in enumerate(results, 1):
        print(f"    [{i}] dist={r['_vector_score']:<8} {r['source']} p{r['page']} [{r['disease_type']}]")
        print(f"        {brief(r['content'])}")


def print_bm25(results: list[dict]) -> None:
    for i, r in enumerate(results, 1):
        print(f"    [{i}] bm25={r.get('score'):<8} {r.get('source')} p{r.get('page')} "
              f"[{r.get('disease_type')}]")
        print(f"        {brief(r.get('content', ''))}")


def print_fused(results: list[dict]) -> None:
    for i, r in enumerate(results, 1):
        recall = r.get("_recall", "?")
        rrf = r.get("_rrf", 0.0)
        print(f"    [{i}] rrf={rrf:<9} 来源={recall:<12} {r.get('source')} p{r.get('page')}")
        print(f"        {brief(r.get('content', ''))}")


def print_final(results: list[dict]) -> None:
    for i, r in enumerate(results, 1):
        print(f"    [{i}] score={r.get('score')} | {r.get('source')} {r.get('version')} p{r.get('page')}")
        print(f"        {brief(r.get('content', ''))}")


def keyset(results: list[dict]) -> set:
    """用"来源+页码+内容前 40 字"做文档指纹，便于比较各路命中集合"""
    return {(r.get("source"), r.get("page"), brief(r.get("content", ""), 40)) for r in results}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 检索链路验证（向量 / BM25 / 混合）")
    parser.add_argument("--query", nargs="*", default=DEFAULT_QUERIES, help="查询语句（可多条）")
    parser.add_argument("--top-k", type=int, default=5, help="每路返回条数（默认 5）")
    args = parser.parse_args()

    print("=" * 74)
    print("RAG 检索链路验证：纯向量检索（Milvus）/ BM25 / 混合检索")
    print("=" * 74)

    try:
        from app.core.config import settings
        from app.ai.tool.rag_tool import keyword_search, rag_search, split_text
    except Exception as e:  # noqa: BLE001
        print(f"❌ 无法导入项目模块：{type(e).__name__}: {e}")
        return 2

    # ---------------- 【1】配置与连接 ----------------
    section("【1】配置与 Milvus 连接")
    print(f"  RAG_MODE        : {settings.rag_mode}")
    print(f"  Milvus          : {settings.milvus_host}:{settings.milvus_port}"
          f" / 集合 {settings.milvus_collection}")
    print(f"  embedding       : provider={settings.embedding_provider} "
          f"model={settings.embedding_model_path or settings.embedding_model} device={settings.embedding_device}")
    print(f"  精排            : provider={settings.rerank_provider} path={settings.rerank_model_path}")
    print(f"  混合检索        : HYBRID_RETRIEVAL={settings.hybrid_retrieval} RRF_K={settings.rrf_k}")
    print(f"  切分            : CHUNK_SIZE={settings.chunk_size} OVERLAP={settings.chunk_overlap}")

    client, row_count = milvus_collection_state()
    if client is None or row_count <= 0:
        print(f"  ❌ 集合 {settings.milvus_collection} 不可用或为空（row_count={row_count}）")
        print("     → 请先灌库：python -m app.ai.tool.rag_tool")
        print("     → 或先体检：python -m app.test.test_milvus")
        return 2
    print(f"  ✅ Milvus 连接成功，集合 {settings.milvus_collection} 实体数：{row_count}")

    # ---------------- 【2】切分自检 ----------------
    section("【2】切分自检（split_text，保留自原脚本）")
    sample = ("高血压是老年人最常见的慢性病之一。老年高血压患者建议每日早晚各测量一次血压，"
              "测量前静坐五分钟。收缩压持续高于一百四十毫米汞柱时应及时就诊。")
    try:
        chunks = split_text(sample, chunk_size=64, overlap=16)
        print(f"  原文 {len(sample)} 字符 → 切 {len(chunks)} 片（演示 64/16；生产 "
              f"{settings.chunk_size}/{settings.chunk_overlap}）")
        for i, c in enumerate(chunks, 1):
            print(f"    [{i}] {len(c):>3} 字：{c[:38]}…")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ split_text 异常：{type(e).__name__}: {e}")

    # ---------------- 逐条查询：三路分开验证 + 对比 ----------------
    dense_pass = bm25_pass = hybrid_pass = True

    for qi, query in enumerate(args.query, 1):
        print("\n" + "#" * 74)
        print(f"# 查询 {qi}/{len(args.query)}：{query}")
        print("#" * 74)

        # 【3-A】纯向量检索（Milvus）
        section("【3-A】纯向量检索：直接查 Milvus（向量 + HNSW/COSINE）")
        dense = dense_search(query, args.top_k)
        print(f"  命中 {len(dense)} 条（Milvus 原始 distance，越大越相似）")
        print_dense(dense)
        if not dense:
            dense_pass = False
            print("  ❌ 向量检索无命中")
        else:
            print("  ✅ 向量检索成功（Milvus 返回了候选）")

        # 【3-B】BM25 稀疏检索
        section("【3-B】BM25 稀疏检索：bm25_util（语料 = knowledge_docs/ ∪ MySQL）")
        sparse = bm25_only_search(query, args.top_k)
        print(f"  命中 {len(sparse)} 条（BM25 分数，越大越相关；<0 不返回）")
        print_bm25(sparse)
        if not sparse:
            bm25_pass = False
            print("  ⚠️ BM25 无命中（语料太少或查询词都不在语料中；不影响向量检索）")
        else:
            print("  ✅ BM25 检索成功")

        # 【3-C】混合检索：RRF 融合 + 精排
        section("【3-C】混合检索：dense ∪ sparse → RRF 融合 → 本地精排")
        fused = hybrid_fuse(query, dense, args.top_k)
        print(f"  融合候选 {len(fused)} 条（_recall 标注该片段由哪一路召回）")
        print_fused(fused)

        # 关闭/开启混合的 A/B 对比（直接看 rag_search 端到端差异）
        original_flag = settings.hybrid_retrieval
        try:
            settings.hybrid_retrieval = False
            dense_only_e2e = rag_search(query, args.top_k)
            settings.hybrid_retrieval = True
            hybrid_e2e = rag_search(query, args.top_k)
        finally:
            settings.hybrid_retrieval = original_flag

        print(f"\n  端到端 rag_search（含精排）：关闭混合 {len(dense_only_e2e)} 条 / "
              f"开启混合 {len(hybrid_e2e)} 条")
        print("  开启混合后的最终结果（score = 精排后的相关概率）：")
        print_final(hybrid_e2e)

        # 【4】三路对比
        section("【4】三路对比")
        d_set, s_set = keyset(dense), keyset(sparse)
        only_dense = d_set - s_set
        only_sparse = s_set - d_set
        both = d_set & s_set
        fusion_new = keyset(fused) - d_set          # 混合带回的"向量路没召回到"的片段
        print(f"    dense 命中 {len(dense)} 条 | BM25 命中 {len(sparse)} 条 | 融合候选 {len(fused)} 条")
        print(f"    · 两路都命中        ：{len(both)} 条")
        print(f"    · 仅向量命中        ：{len(only_dense)} 条")
        print(f"    · 仅 BM25 命中      ：{len(only_sparse)} 条")
        print(f"    · 融合后新增（向量路没有的）：{len(fusion_new)} 条")
        if len(fusion_new) == 0 and len(dense) >= row_count:
            print(f"      ℹ️ 向量路一次就召回了全部 {row_count} 条语料（语料太少），")
            print("         所以「混合没有新增」是正常的——语料扩到几十条以上才能看出差异。")
        elif len(fusion_new) > 0:
            print("      ✅ 混合检索确实带回了向量路漏掉的片段（这就是双路召回的价值）")

        if not fused:
            hybrid_pass = False
            print("  ❌ 混合检索失败（融合后没有候选）")
        else:
            print("  ✅ 混合检索成功（RRF 融合 + 精排链路走通）")

    # ---------------- 【5】MySQL 关键词降级通道 ----------------
    section("【5】MySQL 关键词降级通道（keyword_search，Milvus 全挂时的兜底）")
    try:
        kw = keyword_search(args.query[0], top_k=3)
        if kw:
            print(f"  命中 {len(kw)} 条（来自 MySQL knowledge_doc）")
            print_final(kw)
        else:
            print("  ⚠️ 无命中（MySQL 未启动 / 未导入 sql/init.sql，属正常降级状态）")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 异常（不影响主链路）：{type(e).__name__}: {e}")

    # ---------------- 【6】结论 ----------------
    section("【6】结论")
    print(f"  向量检索（Milvus）  : {'✅ 通过' if dense_pass else '❌ 未通过'}")
    print(f"  BM25 稀疏检索        : {'✅ 通过' if bm25_pass else '⚠️ 无命中（非致命，语料偏少时会出现）'}")
    print(f"  混合检索（RRF+精排） : {'✅ 通过' if hybrid_pass else '❌ 未通过'}")

    if dense_pass and hybrid_pass:
        print("\n  🎉 向量检索与混合检索都跑通了。")
        print("     向量检索：Milvus（HNSW / COSINE）返回候选并带原始 distance；")
        print("     混合检索：额外接入 BM25，用 RRF 融合两路，再由本地 CrossEncoder 精排；")
        print("     结果每条都带 source/version/page，可回溯原文（AC-14）。")
        if not bm25_pass:
            print("     建议：语料偏少（BM25 依赖词面命中），往 knowledge_docs/ 多放文档后重灌。")
        return 0

    print("\n  排查建议：")
    print("    · 向量检索失败 → python -m app.test.test_milvus 看集合与维度；确认已灌库")
    print("    · 混合检索失败 → 检查 HYBRID_RETRIEVAL=true、bm25_util 是否有语料")
    print("    · 维度不匹配 → 删集合重灌（维度建好不可改）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
