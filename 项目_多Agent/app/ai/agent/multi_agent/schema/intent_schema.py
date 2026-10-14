"""
意图识别 / 路由分类 的结构化输出定义

意图识别节点同时输出"意图 + 情感标签"，情感标签用于 FR-05 的情感陪伴能力。
"""
from pydantic import BaseModel, Field


class IntentSchema(BaseModel):
    """意图识别格式化输出结果"""

    intent: str = Field(
        ...,
        description="用户意图，只能是 health_query、medication、emergency、report、emotion、chat 中的一个",
    )
    emotion: str = Field(
        ...,
        description="用户情感标签，只能是 normal、anxious、low、happy、lonely 中的一个",
    )
    confidence: float = Field(
        default=0.5,
        description="意图置信度，0~1 之间的小数",
    )


class RouterSchema(BaseModel):
    """路由分类格式化输出结果"""

    route: str = Field(
        ...,
        description="路由分类，只能是 health、medication、emergency、report、chat 中的一个",
    )
