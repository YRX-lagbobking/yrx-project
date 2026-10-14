"""
健康报告的结构化输出定义

报告章节固定四部分：指标概览、异常与告警汇总、用药依从性、个性化建议。
建议部分必须携带 RAG 溯来源，便于前端展示"依据"。
"""
from pydantic import BaseModel, Field


class ReportSection(BaseModel):
    """报告章节"""

    title: str = Field(..., description="章节标题")
    content: str = Field(..., description="章节正文，Markdown 格式")


class ReportSchema(BaseModel):
    """健康报告格式化输出结果"""

    title: str = Field(..., description="报告标题，例如 '张三 2026 年第 38 周健康周报'")
    summary: str = Field(..., description="一段话总结，包含总体结论与最需要关注的一项")
    sections: list[ReportSection] = Field(
        default_factory=list,
        description="报告章节列表，需包含指标概览、异常与告警汇总、用药依从性、个性化建议",
    )
    advice: str = Field(default="", description="给家属的行动建议（1~3 条）")
