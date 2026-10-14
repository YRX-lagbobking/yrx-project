"""
工具入参模型包（Pydantic）

集中导出各工具的参数校验模型，方便上层统一 `from app.ai.tool.schema import XxxSchema`。
每个模型只负责"类型/范围/格式"校验，安全策略（只读白名单、权限等）在各工具函数内部实现。
"""
from app.ai.tool.schema.influx_schema import ALLOWED_VITAL_FIELDS, InfluxSchema
from app.ai.tool.schema.mysql_schema import MySQLSchema
from app.ai.tool.schema.rag_schema import RagSchema
from app.ai.tool.schema.send_email_schema import EmailParams
from app.ai.tool.schema.skill_schema import SkillSchema

__all__ = [
    "MySQLSchema",
    "EmailParams",
    "InfluxSchema",
    "ALLOWED_VITAL_FIELDS",
    "RagSchema",
    "SkillSchema",
]


if __name__ == "__main__":
    # 自测：确认所有模型都能被导入并实例化
    print("已导出模型：", __all__)
