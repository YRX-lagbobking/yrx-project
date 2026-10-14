"""
知识检索工具（MCP 工具：search_knowledge）

职责：
    检索健康知识库，返回带溯源信息（来源 / 版本 / 页码 / 相关度）的知识片段。

数据来源：
    `app/ai/tool/rag_tool.py::rag_search`（内部：Milvus 向量 → MySQL 关键词降级）
    ⚠️ 本工具只负责"检索"，不负责"静态建议"兜底；
       整体兜底链路由 `app/core/fallback.py::rag_search_with_fallback` 负责（T4）。

返回结构：
    [{"content","source","version","page","score"}]；检索为空返回 []（不抛异常）。
"""
from app.core.logger import logger


def search_knowledge(query: str, top_k: int = 5, disease_type: str | None = None) -> list[dict]:
    """
    检索健康知识库

    参数：
        query        用户问题原文
        top_k        返回条数（默认 5）
        disease_type 病种过滤，例如 高血压 / 糖尿病（可选）
    返回：
        带溯源的知识片段列表；失败返回 []（不抛异常）
    """
    query = (query or "").strip()
    if not query:
        logger.warning("search_knowledge 缺少 query")
        return []

    try:
        top_k = max(1, min(int(top_k or 5), 20))
    except (TypeError, ValueError):
        top_k = 5

    try:
        from app.ai.tool.rag_tool import rag_search

        results = rag_search(query, top_k=top_k, disease_type=disease_type) or []
    except Exception as e:
        logger.error(f"search_knowledge 检索失败：{e}")
        return []

    # 统一字段，保证溯源信息完整（缺字段时给安全默认值）
    normalized: list[dict] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "content": item.get("content", ""),
            "source": item.get("source") or "未知来源",
            "version": item.get("version") or "v1.0",
            "page": item.get("page", ""),
            "score": float(item.get("score", 0.0) or 0.0),
        })
    logger.info(f"search_knowledge 命中 {len(normalized)} 条：{query[:20]}")
    return normalized


if __name__ == "__main__":
    # 自测：Milvus / MySQL 不可用时应返回 []（上层再兜底静态建议），不抛异常
    print("检索：", search_knowledge("高血压老人饮食要注意什么", top_k=2))
    print("空查询：", search_knowledge(""))
