"""
风险判断的结构化输出定义

风险等级四档：
    高危  规则硬阈值命中（心率 >120 或 <50、收缩压 >180 或 <90、血氧 <90% 等）
    中危  趋势异常（连续 3 天同向偏离）或个体离群
    低危  单次轻微偏离
    正常  无异常
"""
from pydantic import BaseModel, Field


class RiskSchema(BaseModel):
    """风险分级格式化输出结果"""

    level: str = Field(
        ...,
        description="风险等级，只能是 高危、中危、低危、正常 中的一个",
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="风险原因，逐条说明命中的规则或趋势，例如 '心率达到 140 次/分，超过安全范围'",
    )
