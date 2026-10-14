"""
MySQL 查询工具入参模型（Pydantic）

用途：
    `app/ai/tool/mysql_tool.py` 的 `mysql_tool` 用 `args_schema=MySQLSchema`
    约束大模型生成的入参，保证只会传入一个字符串形式的只读 SQL。

安全说明（契约第 9 节 + 需求 FR-11）：
    本模型只做"类型与形式"校验；真正的写操作拦截（DELETE/UPDATE/INSERT/
    DROP/CREATE/ALTER）在 mysql_tool 内部以白名单方式二次校验，
    遵循"纵深防御"：模型层拦一道，执行层再拦一道。
"""
from pydantic import BaseModel, Field


class MySQLSchema(BaseModel):
    """mysql_tool 的入参：一条只读 SQL"""

    sql: str = Field(
        ...,
        description="只读 SQL 查询语句（仅允许 SELECT / SHOW / DESC / EXPLAIN，禁止任何写操作）",
        min_length=1,
    )


if __name__ == "__main__":
    # 自测：构造一条合法入参，验证模型可实例化与序列化
    schema = MySQLSchema(sql="SELECT user_id, user_name FROM user_info LIMIT 5")
    print("入参校验通过：", schema.model_dump())
