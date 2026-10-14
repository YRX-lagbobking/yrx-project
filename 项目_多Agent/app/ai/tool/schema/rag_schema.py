"""
RAG 知识检索工具入参模型（Pydantic）

对应契约第 9 节 `rag_search` / `keyword_search`：
    query（必填）、top_k（召回条数）、disease_type（病种过滤，可空）。

业务语义（FR-09）：
    disease_type 用于"元数据过滤"——老人问高血压相关问题时，
    优先只在 `disease_type=高血压` 的文档片段里召回，降低答非所问的概率。
"""
from pydantic import BaseModel, Field


class RagSchema(BaseModel):
    """rag_tool 系列工具的入参"""

    query: str = Field(..., description="用户要检索的健康问题原文", min_length=1)
    top_k: int = Field(5, description="返回的知识片段条数（1~20）", ge=1, le=20)
    disease_type: str | None = Field(
        None,
        description="病种过滤条件，例如 高血压 / 糖尿病 / 冠心病；不传表示不过滤",
    )


if __name__ == "__main__":
    # 自测：默认值 + 显式传参 + 越界拦截
    print(RagSchema(query="高血压老人平时要注意什么").model_dump())
    print(RagSchema(query="血糖高怎么办", top_k=3, disease_type="糖尿病").model_dump())
    try:
        RagSchema(query="x", top_k=0)
        print("❌ top_k 越界未被拦截")
    except Exception as e:
        print(f"✅ top_k 越界已被拦截：{type(e).__name__}")
