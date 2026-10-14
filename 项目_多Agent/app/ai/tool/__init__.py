"""
AI 工具包（LangChain Tools + 领域能力）

模块划分：
    mysql_tool.py      只读 SQL 查询（白名单 + 写操作拦截）
    send_email_tool.py 邮件发送（普通函数 + LangChain Tool 双出口）
    influx_tool.py     InfluxDB 3 生命体征时序（读写 + 演示模拟器 + 本地 JSON 降级）
    rag_tool.py        健康知识库 RAG（Milvus 向量 + MySQL 关键词 + 混合精排）
    mcp_tool.py        MCP 工具客户端（SDK → HTTP → 本地函数三级降级）
    skill_tool.py      Skill 渐进式加载
    schema/            各工具的 Pydantic 入参模型

设计约定（契约第 0、9 节）：
    - 本包所有公开函数**永不向上抛异常**，失败返回错误字符串或降级结果；
    - 所有第三方可选依赖（influxdb_client_3 / pymilvus / mcp / requests / pypdf / python-docx）
      一律函数内延迟导入 + try/except ImportError，保证依赖缺失时本包仍可 import。
"""

__all__ = [
    "mysql_tool",
    "send_email_tool",
    "influx_tool",
    "rag_tool",
    "mcp_tool",
    "skill_tool",
]


if __name__ == "__main__":
    # 自测：逐个导入子模块，验证"依赖缺失也能 import"
    import importlib

    for module_name in __all__:
        try:
            importlib.import_module(f"app.ai.tool.{module_name}")
            print(f"✅ app.ai.tool.{module_name} 导入成功")
        except Exception as e:
            print(f"❌ app.ai.tool.{module_name} 导入失败：{type(e).__name__}: {e}")
