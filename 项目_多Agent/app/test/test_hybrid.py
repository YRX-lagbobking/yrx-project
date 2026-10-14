"""
混合检索自测脚本（BM25 稀疏召回 + RRF 融合 + 本地精排）

运行方式（在项目根目录执行）：
    python -m app.test.test_hybrid

覆盖：
    1. 内置纯 Python BM25 排序是否正确（不依赖 MySQL / Milvus / rank_bm25）
    2. jieba / rank_bm25 是否可用（不可用时自动降级，仅提示）
    3. RRF 融合逻辑：两路并集、双路命中优先、来源标注（打桩，不连真实存储）
    4. 本地精排可达性回归：未配置云端 Key 时本地 CrossEncoder 仍然生效
    5. 真实检索入口 rag_search（需要 Milvus + MySQL + 本地模型，失败只提示不报错）
"""
import os
import sys

# 允许直接 `python app/test/test_hybrid.py` 运行
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.ai.tool import bm25_util
from app.core.config import settings


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def test_builtin_bm25() -> None:
    """内置 BM25（纯标准库）排序是否正确"""
    docs = [
        {"content": "高血压患者应低盐饮食，每日食盐不超过 5 克", "source": "高血压指南", "page": 1},
        {"content": "糖尿病低血糖时立即进食 15 克快速升糖食物", "source": "糖尿病指南", "page": 1},
        {"content": "老年人用药按时按量，漏服不可加倍补服", "source": "用药安全", "page": 1},
    ]
    tokens = [bm25_util.tokenize(d["content"]) for d in docs]
    index = bm25_util._PureBM25(tokens)
    scores = index.get_scores(bm25_util.tokenize("高血压 饮食"))
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    for i in order:
        print(f"  {scores[i]:8.4f}  {docs[i]['source']}")
    top = order[0]
    print(f"  分词结果：{bm25_util.tokenize('高血压患者应低盐饮食')}")
    assert top == 0, "BM25 排序错误：高血压相关文档应排第一"
    print("  ✅ 内置 BM25 排序正确")


def test_dependencies() -> None:
    """可选依赖可用性（缺失不影响运行，只是效果或速度差一点）"""
    for name in ("jieba", "rank_bm25"):
        try:
            __import__(name)
            print(f"  ✅ {name} 已安装")
        except ImportError:
            print(f"  ⚠️  {name} 未安装 —— 已自动降级（{'2-gram 分词' if name == 'jieba' else '内置纯 Python BM25'}）")


def test_rrf_fusion() -> None:
    """RRF 融合逻辑（打桩两路召回结果，不连真实存储）"""
    from app.ai.tool import rag_tool as rt

    dense = [
        {"content": "D1 只在向量路出现", "source": "dense1", "disease_type": "通用"},
        {"content": "BOTH 两路都有", "source": "both", "disease_type": "通用"},
    ]
    sparse = [
        {"content": "BOTH 两路都有", "source": "both", "disease_type": "通用", "score": 9.0},
        {"content": "S2 只在 BM25 路出现", "source": "sparse2", "disease_type": "通用", "score": 5.0},
    ]
    original = bm25_util.bm25_search
    bm25_util.bm25_search = lambda q, top_k=5: sparse        # 打桩
    try:
        merged = rt._fuse_dense_and_bm25("测试问题", dense, top_k=3)
    finally:
        bm25_util.bm25_search = original

    for item in merged:
        print(f"  {item['source']:10s} rrf={item.get('_rrf'):.5f} 来源={item.get('_recall')}")
    names = [m["source"] for m in merged]
    assert {"dense1", "both", "sparse2"} <= set(names), "融合应包含两路并集"
    assert names[0] == "both", "两路都命中的文档应排第一"
    print("  ✅ RRF 融合正确（并集 + 双路命中优先 + 来源标注）")

    # 关闭开关 → 回到单路
    settings.hybrid_retrieval = False
    try:
        single = rt._fuse_dense_and_bm25("测试问题", dense, top_k=3)
        assert [x["source"] for x in single] == ["dense1", "both"], "开关关闭时应原样返回单路结果"
        print("  ✅ HYBRID_RETRIEVAL=false 时退回单路向量召回")
    finally:
        settings.hybrid_retrieval = True


def test_local_rerank_reachable() -> None:
    """回归测试：未配置云端 rerank Key 时，本地 CrossEncoder 仍应生效"""
    from app.ai.tool import rag_tool as rt

    class _StubEncoder:
        def predict(self, pairs):
            # 故意把"稀疏独有"打高分，用以判断是否真的走了本地精排
            return [-10.0 if "向量独有" in p[1] else 10.0 for p in pairs]

    original_getter = rt._get_cross_encoder
    original_provider = settings.rerank_provider
    original_key = settings.dashscope_api_key
    rt._get_cross_encoder = lambda: _StubEncoder()
    settings.rerank_provider = "local"
    settings.dashscope_api_key = ""            # 模拟没有云端 Key
    try:
        out = rt._rerank("测试问题", [
            {"content": "D1 向量独有", "source": "dense1", "score": 0.9, "_vector_score": 0.9},
            {"content": "S2 稀疏独有", "source": "sparse2", "score": 0.1},
        ], top_k=2)
        for item in out:
            print(f"  {item['source']:10s} score={item['score']}")
        assert out[0]["source"] == "sparse2", "本地精排未生效（控制流可能被云端分支短路）"
        print("  ✅ 本地精排生效（无云端 Key 也能走通）")
    finally:
        rt._get_cross_encoder = original_getter
        settings.rerank_provider = original_provider
        settings.dashscope_api_key = original_key


def test_real_search() -> None:
    """真实检索入口（需要 Milvus/MySQL/本地模型；任一缺失只提示降级）"""
    from app.ai.tool.rag_tool import rag_search

    question = "老人血压高平时要注意什么"
    try:
        hits = rag_search(question, top_k=3)
    except Exception as e:
        print(f"  ⚠️  rag_search 异常（不影响其它功能）：{type(e).__name__}: {e}")
        return
    if not hits:
        print("  ⚠️  无命中 —— 检查 Milvus 是否已灌库（build_knowledge_base）或 RAG_MODE 配置")
        return
    for item in hits:
        print(f"  score={item.get('score')} | {item.get('source')} p{item.get('page')} | {item.get('content', '')[:34]}")


if __name__ == "__main__":
    section("【1】内置 BM25 排序（纯标准库，必过）")
    test_builtin_bm25()

    section("【2】可选依赖可用性")
    test_dependencies()

    section("【3】RRF 融合（打桩验证，不连存储）")
    test_rrf_fusion()

    section("【4】本地精排可达性（回归测试）")
    test_local_rerank_reachable()

    section("【5】真实检索 rag_search（需要 Milvus + 本地模型）")
    test_real_search()

    print("\n全部自测通过 ✅")
