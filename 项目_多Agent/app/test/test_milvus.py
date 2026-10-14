"""
Milvus 数据体检脚本（检查集合是否存在、有多少数据、索引是否就绪、抽样看内容）

运行方式（项目根目录）：
    python -m app.test.test_milvus              # 快速体检：连接 / 集合 / 数量 / 索引 / 抽样
    python -m app.test.test_milvus --deep       # 追加：向量维度一致性 + 真实检索验证（会加载本地模型，较慢）
    python -m app.test.test_milvus --sample 10  # 抽样条数（默认 5）

检查项：
    【1】连接与服务端版本
    【2】集合清单（health_knowledge / long_memory）
    【3】health_knowledge 详细：schema 字段与维度、实体数、索引、load 状态
    【4】抽样数据（内容/来源/页码），并统计各来源的片段数
    【5】--deep：schema 向量维度 vs 当前 embedding 模型实际维度（防维度不匹配）
    【6】--deep：真实检索一次（混合召回 + 精排），看能否召回

退出码：
    0 = 有数据；1 = 集合不存在或为空；2 = 连不上 Milvus
"""
import argparse
import os
import sys
from collections import Counter

# 允许直接 `python app/test/test_milvus.py` 运行，并统一切到项目根
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from app.core.config import settings  # noqa: E402

try:
    from pymilvus import DataType, MilvusClient
except ImportError:
    print("❌ 未安装 pymilvus，无法体检。请先安装：pip install 'pymilvus>=2.6,<3.0'")
    sys.exit(2)


def line(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def type_name(value) -> str:
    """把字段类型（int 或 DataType）转成可读名字"""
    try:
        return DataType(value).name
    except Exception:  # noqa: BLE001
        return str(value)


def connect() -> MilvusClient | None:
    """建立连接并打印服务端版本"""
    line("【1】连接与版本")
    uri = f"http://{settings.milvus_host}:{settings.milvus_port}"
    try:
        client = MilvusClient(uri=uri)
        try:
            version = client.get_server_version()
        except Exception:  # noqa: BLE001
            version = "（该版本不支持 get_server_version）"
        print(f"  ✅ 已连接 {uri}")
        print(f"     服务端版本：{version}")
        return client
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ 无法连接 {uri}：{type(e).__name__}: {e}")
        print("     排查：docker ps 看 milvus 是否在跑；Test-NetConnection 127.0.0.1 -Port 19530")
        return None


def check_health_knowledge(client: MilvusClient, sample: int, stats_limit: int) -> int:
    """返回 0=有数据 / 1=空或不存在"""
    name = settings.milvus_collection

    line("【3】集合 health_knowledge 详情")
    if name not in client.list_collections():
        print(f"  ❌ 集合 {name} 不存在 —— 还没灌库")
        print("     灌库命令：")
        print("       python -c \"from app.ai.tool.rag_tool import build_knowledge_base;"
              " print('入库片段数:', build_knowledge_base('knowledge_docs'))\"")
        return 1

    # ---------- schema ----------
    info = client.describe_collection(name)
    fields = info.get("fields", [])
    dim = None
    print(f"  ✅ 集合存在，schema 共 {len(fields)} 个字段：")
    for f in fields:
        params = f.get("params") or {}
        if f.get("name") == "vector" and params.get("dim"):
            dim = params["dim"]
        flag = "（自增主键）" if f.get("auto_id") else ""
        print(f"     - {f.get('name'):<14} {type_name(f.get('type')):<16} {params or ''}{flag}")

    # ---------- 实体数 ----------
    try:
        stats = client.get_collection_stats(name)
        row_count = int(stats.get("row_count", 0))
    except Exception as e:  # noqa: BLE001
        row_count = -1
        print(f"  ⚠️  读取实体数失败：{type(e).__name__}: {e}")
    if row_count == 0:
        print("  ❌ 集合存在但里面是空的（row_count = 0）—— 灌库没成功或已被清空")
        return 1
    if row_count > 0:
        print(f"  ✅ 实体数（片段数）：{row_count}")

    # ---------- 索引 ----------
    try:
        indexes = client.list_indexes(name)
        print(f"  ·  索引：{indexes}")
        for idx in indexes:
            detail = client.describe_index(name, idx)
            print(f"     - {idx}: index_type={detail.get('index_type')} "
                  f"metric_type={detail.get('metric_type')} params={detail.get('params')}")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  读取索引失败：{type(e).__name__}: {e}")

    # ---------- load 状态 ----------
    try:
        state = client.get_load_state(name)
        print(f"  ·  load 状态：{state}")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  读取 load 状态失败：{type(e).__name__}: {e}")

    # ---------- 抽样 ----------
    line("【4】抽样数据")
    rows: list = []
    for attempt in ("直接查询", "load 后重试"):
        try:
            # 用主键表达式取"全部"，避免 filter="" 报错
            rows = client.query(
                collection_name=name,
                filter="id >= 0",
                output_fields=["content", "source", "page", "disease_type"],
                limit=max(sample, 1),
            )
            break
        except Exception as e:  # noqa: BLE001
            if attempt == "直接查询":
                print(f"  ·  直接查询失败（{type(e).__name__}），尝试 load_collection 后重试…")
                try:
                    client.load_collection(name)
                except Exception as e2:  # noqa: BLE001
                    print(f"  ⚠️  load 失败：{type(e2).__name__}: {e2}")
                    break
            else:
                print(f"  ⚠️  抽样查询失败：{type(e).__name__}: {e}")
                break

    if not rows:
        print("  ⚠️  抽样没有取到数据（集合可能未 load）")
    for i, r in enumerate(rows, start=1):
        content = (r.get("content") or "").replace("\n", " ")
        print(f"  [{i}] {r.get('source')} p{r.get('page')} [{r.get('disease_type')}]")
        print(f"      {content[:70]}")

    # ---------- 各来源片段数 ----------
    try:
        all_rows = client.query(collection_name=name, filter="id >= 0",
                                output_fields=["source"], limit=stats_limit)
        counter = Counter((r.get("source") or "未知") for r in all_rows)
        print(f"\n  ·  各来源片段数（统计前 {len(all_rows)} 条，上限 {stats_limit}）：")
        for src, cnt in counter.most_common():
            print(f"     {cnt:>4} 条  {src}")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  来源统计失败：{type(e).__name__}: {e}")

    globals()["_DIM"] = dim
    return 0


def check_long_memory(client: MilvusClient) -> None:
    line("【5】集合 long_memory（L3 长期记忆）")
    name = settings.long_memory_collection
    if name not in client.list_collections():
        print(f"  ⚠️  集合 {name} 还不存在 —— 正常，聊过一轮天后会自动创建")
        return
    try:
        row_count = int(client.get_collection_stats(name).get("row_count", 0))
        print(f"  ✅ 集合存在，实体数：{row_count}（每轮对话后写入长期记忆片段）")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  读取失败：{type(e).__name__}: {e}")


def deep_checks(client: MilvusClient) -> None:
    """向量维度一致性 + 真实检索"""
    line("【6】向量维度一致性检查")
    schema_dim = globals().get("_DIM")
    print(f"  集合 schema 的向量维度：{schema_dim}")
    try:
        from app.ai.model.my_model import MyModel

        print(f"  当前 embedding：provider={settings.embedding_provider} path={settings.embedding_model_path}")
        vector = MyModel.get_embedding_model().embed_query("维度探测")
        model_dim = len(vector)
        print(f"  模型实际输出维度：{model_dim}")
        if schema_dim and int(schema_dim) == int(model_dim):
            print("  ✅ 维度一致，不会出现 dimension mismatch")
        else:
            print("  ❌ 维度不一致！插入/检索会报维度错误 ——")
            print(f"     解决：删除集合后重新灌库（集合维度建好就不能改）")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  模型维度探测失败：{type(e).__name__}: {e}")

    line("【7】真实检索验证（混合召回 dense∪BM25 → RRF → 本地精排）")
    try:
        from app.ai.tool.rag_tool import rag_search

        question = "老人血压高平时要注意什么"
        print(f"  查询：{question}")
        hits = rag_search(question, top_k=3)
        if not hits:
            print("  ❌ 没有召回任何结果（检查 RAG_MODE / 集合是否有数据 / 维度是否一致）")
            return
        for i, h in enumerate(hits, start=1):
            print(f"  [{i}] score={h.get('score')} | {h.get('source')} p{h.get('page')}")
            print(f"      {(h.get('content') or '')[:60]}")
        print("  ✅ 检索链路通了（带 source/page 溯源，说明有真实数据）")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  检索异常：{type(e).__name__}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Milvus 数据体检")
    parser.add_argument("--deep", action="store_true", help="追加维度一致性与真实检索检查（会加载本地模型）")
    parser.add_argument("--sample", type=int, default=5, help="抽样条数（默认 5）")
    parser.add_argument("--stats-limit", type=int, default=2000, help="来源统计最多读取条数（默认 2000）")
    args = parser.parse_args()

    print("=" * 74)
    print("Milvus 数据体检")
    print("=" * 74)
    print(f"  目标   : {settings.milvus_host}:{settings.milvus_port}")
    print(f"  知识集合: {settings.milvus_collection}")
    print(f"  记忆集合: {settings.long_memory_collection}")
    print(f"  RAG_MODE: {settings.rag_mode}")

    client = connect()
    if client is None:
        return 2

    line("【2】集合清单")
    try:
        collections = client.list_collections()
        print(f"  {collections if collections else '（空，一个集合都没有）'}")
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ 读取集合清单失败：{type(e).__name__}: {e}")
        return 2

    result = check_health_knowledge(client, args.sample, args.stats_limit)

    if args.deep:
        check_long_memory(client)
        deep_checks(client)
    else:
        line("【5】集合 long_memory（L3 长期记忆）")
        check_long_memory(client)
        print("\n  （加 --deep 可做向量维度一致性检查与真实检索验证）")

    # ---------- 结论 ----------
    line("体检结论")
    if result == 0:
        print("  ✅ Milvus 里有数据，知识库可用")
        print("\n  下一步：")
        print("    · 端到端验证：python -m app.test.test_hybrid")
        print("    · 启动后端：python -m app.main  然后访问 http://localhost:8000")
    else:
        print("  ❌ Milvus 里没有可用的知识库数据")
        print("\n  下一步：")
        print("    1) 确认 Milvus 三件套在跑：docker ps（milvus / milvus-etcd / milvus-minio 都 healthy）")
        print("    2) 灌库：python -m app.ai.tool.rag_tool  或")
        print("       python -c \"from app.ai.tool.rag_tool import build_knowledge_base;"
              " print('入库片段数:', build_knowledge_base('knowledge_docs'))\"")
        print("    3) 再跑本脚本复查")
    return result


if __name__ == "__main__":
    sys.exit(main())
