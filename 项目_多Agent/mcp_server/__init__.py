"""
MCP Server 包

组成：
    mcp_health_server.py  服务入口（优先官方 mcp SDK FastMCP；未安装则降级 FastAPI）
    tools/                七个纯函数工具实现（可脱离 MCP 直接调用、单测、被本地降级复用）

对外协议（需求 9.3）：
    统一 `Authorization: Bearer <MCP_API_KEY>` 鉴权，白名单工具表，白名单外直接拒绝。
"""

__all__ = ["mcp_health_server"]


if __name__ == "__main__":
    # 自测：只打印包说明，不启动服务（避免自测时占用 9000 端口）
    print(__doc__)
