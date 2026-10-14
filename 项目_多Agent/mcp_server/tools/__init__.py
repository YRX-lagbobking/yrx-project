"""
MCP 工具实现包（纯函数层）

设计原则：
    1. 这里全是**纯函数**，不依赖 MCP 框架，可脱离 MCP Server 直接调用与单测；
    2. 同时被两个地方复用：
       - `mcp_server/mcp_health_server.py`（通过 MCP 协议暴露给 Agent）；
       - `app/ai/tool/mcp_tool.py`（远程不可用时的**本地降级实现**）；
    3. 一律"永不抛异常"：数据源不可用时返回空结构 / 降级结果，并记录日志。

函数清单：
    profile_tool.get_user_profile(name=None, user_id=None) -> dict
    medication_tool.get_medication_schedule(user_id=None, name=None) -> list[dict]
    vitals_tool.get_vitals(user_id, field=None, days=7) -> dict
    knowledge_tool.search_knowledge(query, top_k=5, disease_type=None) -> list[dict]
    report_tool.generate_report(user_id, type="daily") -> str
    notify_tool.send_sms(to, content) -> dict
    notify_tool.send_email(to, subject, content) -> dict
"""

__all__ = [
    "common",
    "profile_tool",
    "medication_tool",
    "vitals_tool",
    "knowledge_tool",
    "report_tool",
    "notify_tool",
]


if __name__ == "__main__":
    # 自测：逐个导入，验证中间件缺失时也能 import
    import importlib

    for module_name in __all__:
        try:
            importlib.import_module(f"mcp_server.tools.{module_name}")
            print(f"✅ mcp_server.tools.{module_name} 导入成功")
        except Exception as e:
            print(f"❌ mcp_server.tools.{module_name} 导入失败：{type(e).__name__}: {e}")
