"""
MCP 工具客户端（契约第 9 节 / 需求 FR-11、FR-15）

三级降级链路（对应需求 5.15"工具"那一行：MCP 远程工具 → 本地实现 → 缓存 → 待处理队列）：

    第 1 级：官方 mcp SDK 客户端（Streamable HTTP / SSE）调用远程 MCP Server；
    第 2 级：`requests` POST `{settings.mcp_server_url}/tools/call`（自带 sse 版 server 的 HTTP 出口）；
    第 3 级：直接调用 `mcp_server/tools/*` 里的**同名本地纯函数**（LOCAL_TOOL_MAP 映射）；
    最后一级：全部失败时返回 `{"ok": False, ...}`，由 `app/core/fallback.py` 继续兜到缓存/待处理队列。

设计要点：
    1. `MCPClient` 是**单例**（`get_client()`），避免每次工具调用都重新建连；
    2. 所有第三方依赖（mcp / requests）都在**函数内延迟导入**，未安装时模块照样能 import；
    3. 任何网络/协议异常都被捕获，永远不会把异常抛给上层 Agent。
"""
import asyncio
import json
from typing import Any, Callable

from app.core.config import settings
from app.core.logger import logger

# MCP Server 白名单工具（与契约第 21 节、mcp_server/mcp_health_server.py 的 ALLOWED_TOOLS 一致）
ALLOWED_TOOLS = (
    "get_user_profile",
    "get_medication_schedule",
    "get_vitals",
    "search_knowledge",
    "generate_report",
    "send_sms",
    "send_email",
)

# 网络请求超时（秒）
REQUEST_TIMEOUT = 10


# ============================================================
# 本地降级映射：工具名 → mcp_server/tools 里的纯函数
# ============================================================
def _local_tool_map() -> dict[str, Callable[..., Any]]:
    """
    构造"MCP 工具名 → 本地纯函数"的映射表

    为什么要延迟构造：
        `mcp_server.tools.*` 内部会 import app.dao / app.ai.tool，
        延迟到真正降级时再导入，可以把模块导入期的循环依赖风险降到最低。
    找不到某个函数时跳过（只影响该工具的降级能力，不影响其它工具）。
    """
    mapping: dict[str, Callable[..., Any]] = {}
    candidates = [
        ("get_user_profile", "mcp_server.tools.profile_tool", "get_user_profile"),
        ("get_medication_schedule", "mcp_server.tools.medication_tool", "get_medication_schedule"),
        ("get_vitals", "mcp_server.tools.vitals_tool", "get_vitals"),
        ("search_knowledge", "mcp_server.tools.knowledge_tool", "search_knowledge"),
        ("generate_report", "mcp_server.tools.report_tool", "generate_report"),
        ("send_sms", "mcp_server.tools.notify_tool", "send_sms"),
        ("send_email", "mcp_server.tools.notify_tool", "send_email"),
    ]
    for tool_name, module_path, func_name in candidates:
        try:
            module = __import__(module_path, fromlist=[func_name])
            mapping[tool_name] = getattr(module, func_name)
        except Exception as e:                            # 单个映射失败不影响其它工具
            logger.warning(f"本地降级映射缺失 {tool_name}（{module_path}）：{e}")
    return mapping


class MCPClient:
    """
    MCP 工具调用客户端（单例）

    使用方式：
        result = await MCPClient.get_client().call_tool("get_vitals", {"user_id": "elder_001"})
        # result == {"ok": True, "data": {...}, "msg": "..."}
    """

    _instance: "MCPClient | None" = None

    def __init__(self) -> None:
        # 远程可用性标记：第一次失败后短时间内不再尝试远程（避免每次调用都等超时）
        self._remote_failed: bool = False
        # 本地函数映射（首次降级时才真正导入）
        self._local_map: dict[str, Callable[..., Any]] | None = None

    # ---------------- 单例入口 ----------------
    @staticmethod
    def get_client() -> "MCPClient":
        """获取全局唯一客户端实例"""
        if MCPClient._instance is None:
            MCPClient._instance = MCPClient()
        return MCPClient._instance

    # ---------------- 对外接口 ----------------
    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """
        调用 MCP 工具

        参数：
            name      工具名（必须在 ALLOWED_TOOLS 白名单内）
            arguments 工具入参
        返回：
            {"ok": bool, "data": Any, "msg": str}    —— 永不抛异常
        """
        arguments = arguments or {}

        # 0) 白名单校验（本地也拦一道，防止 LLM 幻觉调用不存在的工具）
        if name not in ALLOWED_TOOLS:
            logger.warning(f"MCP 工具名不在白名单：{name}")
            return {"ok": False, "data": None, "msg": f"不支持的工具：{name}"}

        # 1) 官方 SDK
        if not self._remote_failed:
            result = await self._call_via_sdk(name, arguments)
            if result is not None:
                return result

            # 2) HTTP 直连（sse 版 server 的 HTTP 出口）
            result = await self._call_via_http(name, arguments)
            if result is not None:
                return result

            # 远程两种方式都失败了 → 标记，后续直接走本地降级
            self._remote_failed = True
            logger.warning("MCP 远程调用失败，后续将优先使用本地实现（降级）")

        # 3) 本地纯函数降级
        return self._call_local(name, arguments)

    # ---------------- 第 1 级：官方 mcp SDK ----------------
    async def _call_via_sdk(self, name: str, arguments: dict) -> dict | None:
        """
        用官方 mcp SDK 调用远程工具

        返回 None 表示"该方式不可用，请尝试下一级"；返回 dict 表示调用有结果。
        """
        try:
            from mcp import ClientSession                        # 可选依赖：延迟导入
        except ImportError:
            return None

        try:
            # 优先 Streamable HTTP（MCP 新版标准传输）
            try:
                from mcp.client.streamable_http import streamablehttp_client

                transport_cm = streamablehttp_client(f"{settings.mcp_server_url}/mcp")
            except ImportError:
                # 老版本 SDK 只有 SSE 传输
                from mcp.client.sse import sse_client

                transport_cm = sse_client(f"{settings.mcp_server_url}/sse")

            async with transport_cm as transport:
                # 不同版本 SDK 的返回值结构略有差异：可能是 (read, write) 或 (read, write, get_session_id)
                read_stream, write_stream = transport[0], transport[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
                    return {
                        "ok": True,
                        "data": _sdk_result_to_data(result),
                        "msg": f"MCP SDK 调用成功：{name}",
                    }
        except Exception as e:
            logger.warning(f"MCP SDK 调用失败（{name}）：{e}")
            return None

    # ---------------- 第 2 级：HTTP POST /tools/call ----------------
    async def _call_via_http(self, name: str, arguments: dict) -> dict | None:
        """
        用 requests 直接 POST `{settings.mcp_server_url}/tools/call`

        放在线程池里跑（`asyncio.to_thread`），避免同步 HTTP 阻塞事件循环。
        """
        try:
            import requests                                      # 可选依赖：延迟导入
        except ImportError:
            return None

        def _do_post() -> dict | None:
            try:
                response = requests.post(
                    f"{settings.mcp_server_url.rstrip('/')}/tools/call",
                    json={"name": name, "arguments": arguments},
                    headers={
                        "Authorization": f"Bearer {settings.mcp_api_key}",   # 契约要求的鉴权头
                        "Content-Type": "application/json",
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                if response.status_code != 200:
                    logger.warning(f"MCP HTTP 调用返回 {response.status_code}：{response.text[:200]}")
                    return None
                body = response.json()
                # 兼容两种返回：{"ok":..,"data":..} 与 {"result":..}
                if isinstance(body, dict) and "ok" in body:
                    return {
                        "ok": bool(body.get("ok")),
                        "data": body.get("data"),
                        "msg": body.get("msg") or f"MCP HTTP 调用完成：{name}",
                    }
                return {"ok": True, "data": body, "msg": f"MCP HTTP 调用成功：{name}"}
            except Exception as e:
                logger.warning(f"MCP HTTP 调用失败（{name}）：{e}")
                return None

        try:
            # 同步 requests 丢到线程池执行，避免阻塞异步节点
            return await asyncio.to_thread(_do_post)
        except Exception as e:
            logger.warning(f"MCP HTTP 调用异常（{name}）：{e}")
            return None

    # ---------------- 第 3 级：本地纯函数 ----------------
    def _call_local(self, name: str, arguments: dict) -> dict:
        """降级为直接调用 `mcp_server/tools` 里的同名本地函数"""
        if self._local_map is None:
            self._local_map = _local_tool_map()

        func = self._local_map.get(name)
        if func is None:
            logger.error(f"本地降级不存在该工具：{name}")
            return {"ok": False, "data": None, "msg": f"MCP 不可用且本地无 {name} 的降级实现"}

        try:
            data = func(**(arguments or {}))
            logger.info(f"MCP 工具 {name} 已降级为本地实现")
            return {"ok": True, "data": data, "msg": f"已降级为本地实现：{name}"}
        except TypeError as e:
            # 入参不匹配（LLM 传了多余/缺失参数）→ 明确提示，避免误判为服务故障
            logger.error(f"本地降级调用 {name} 入参错误：{e}")
            return {"ok": False, "data": None, "msg": f"调用 {name} 的参数不正确：{e}"}
        except Exception as e:
            logger.error(f"本地降级调用 {name} 失败：{e}")
            return {"ok": False, "data": None, "msg": f"调用 {name} 失败：{e}"}


def _sdk_result_to_data(result: Any) -> Any:
    """
    把 mcp SDK 的 CallToolResult 转成 Python 数据

    兼容三种情况：
        1. structuredContent（新版 SDK，已经是 dict）；
        2. content 列表里的 TextContent（内容是 JSON 字符串）；
        3. 其它 → 直接 str()。
    """
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if structured is not None:
        return structured

    contents = getattr(result, "content", None) or []
    texts: list[str] = []
    for item in contents:
        text = getattr(item, "text", None)
        if text:
            texts.append(text)
    joined = "\n".join(texts)
    if not joined:
        return str(result)
    # 优先尝试按 JSON 解析（本地 server 返回的是 json.dumps 字符串）
    try:
        return json.loads(joined)
    except Exception:
        return joined


# ---------------- 模块级便捷函数 ----------------
async def call_mcp_tool(name: str, arguments: dict) -> dict:
    """
    调用 MCP 工具的便捷函数（供节点/任务层直接 import 使用）

    等价于 `await MCPClient.get_client().call_tool(name, arguments)`。
    """
    return await MCPClient.get_client().call_tool(name, arguments)


if __name__ == "__main__":
    # 自测：MCP Server 未启动时应降级为本地实现（本地 DB 也没有则返回 ok=False，不抛异常）
    async def _demo() -> None:
        print("白名单工具：", ALLOWED_TOOLS)
        print("非白名单调用：", await call_mcp_tool("drop_database", {}))
        print("get_vitals 降级结果：", await call_mcp_tool("get_vitals", {"user_id": "elder_demo", "days": 1}))
        print("get_user_profile 降级结果：", await call_mcp_tool("get_user_profile", {"user_id": "elder_demo"}))
        print("send_sms 降级结果：", await call_mcp_tool("send_sms", {"to": "13800000000", "content": "测试短信"}))

    asyncio.run(_demo())
