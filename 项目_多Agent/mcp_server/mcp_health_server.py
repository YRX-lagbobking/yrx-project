"""
MCP Server 入口（契约第 21 节 / 需求 9.3、FR-11）

对外提供 7 个白名单工具：
    get_user_profile、get_medication_schedule、get_vitals、search_knowledge、
    generate_report、send_sms、send_email

两条实现路线（自动选择，见 `_build_sdk_server()`）：

    路线 A（首选）：官方 mcp SDK 的 `FastMCP`
        mcp = FastMCP("health-butler-tools", host="0.0.0.0", port=9000)
        `@mcp.tool()` 注册 7 个工具；`mcp.run(transport="sse")` 启动（SSE 传输，端口 9000）。

    路线 B（降级）：未安装 mcp SDK 时，用 FastAPI 复刻同一套 HTTP 语义：
        GET  /healthz      健康检查（无需鉴权）
        GET  /tools/list   工具清单（需鉴权）
        POST /tools/call   工具调用（需鉴权）
        `uvicorn` 直接启动，端口 9000。

两条路线都通过 `KeyVerifier` + `ALLOWED_TOOLS` 做**双层防护**：
    1. `Authorization: Bearer <MCP_API_KEY>` 必须与 `settings.mcp_api_key` 一致；
    2. 工具名必须在 `ALLOWED_TOOLS` 白名单内，白名单外直接拒绝（AC-21）。
"""
import json
import os
from typing import Any

from app.core.config import settings
from app.core.logger import logger
from mcp_server.tools import (
    knowledge_tool,
    medication_tool,
    notify_tool,
    profile_tool,
    report_tool,
    vitals_tool,
)
from mcp_server.tools.common import extract_bearer_token

# 服务监听端口（契约第 21 节锁定 9000）
SERVER_PORT = 9000

# 白名单工具集合：白名单外的调用一律拒绝（工具权限边界，FR-11）
ALLOWED_TOOLS = {
    "get_user_profile",
    "get_medication_schedule",
    "get_vitals",
    "search_knowledge",
    "generate_report",
    "send_sms",
    "send_email",
}

# 工具说明（供 /tools/list 与调试使用）
TOOL_DESCRIPTIONS = {
    "get_user_profile": "查询老人健康档案（姓名或用户编号）",
    "get_medication_schedule": "查询老人用药计划与今日待服清单",
    "get_vitals": "查询老人生命体征时序数据与统计",
    "search_knowledge": "检索健康知识库，返回带溯源的知识片段",
    "generate_report": "生成老人健康日报 / 周报正文",
    "send_sms": "发送短信通知（模拟通道：写日志 + 落队列）",
    "send_email": "发送邮件通知（真实 SMTP）",
}


class KeyVerifier:
    """
    MCP 工具调用鉴权器

    规则：
        - 请求头必须是 `Authorization: Bearer <MCP_API_KEY>`；
        - 与 `settings.mcp_api_key` 不一致 → 拒绝（AC-21）；
        - 服务端未配置 Key → 视为配置错误，一律拒绝（安全默认：不放开）。
    """

    @staticmethod
    def verify_key(authorization: str) -> bool:
        """校验 Authorization 头，返回是否通过"""
        token = extract_bearer_token(authorization)
        expected = (settings.mcp_api_key or "").strip()
        if not expected:
            logger.error("MCP_API_KEY 未配置，拒绝所有工具调用（安全默认）")
            return False
        if not token:
            logger.warning("MCP 调用缺少 Authorization 头")
            return False
        if token != expected:
            logger.warning("MCP_API_KEY 校验失败，拒绝调用")
            return False
        return True

    @staticmethod
    def is_tool_allowed(name: str) -> bool:
        """白名单校验：白名单外的工具名直接拒绝"""
        allowed = name in ALLOWED_TOOLS
        if not allowed:
            logger.warning(f"工具 {name} 不在白名单内，拒绝调用")
        return allowed


def tool_list() -> list[dict]:
    """返回工具清单（供 /tools/list 与 Agent 侧提示词使用）"""
    return [
        {"name": name, "description": TOOL_DESCRIPTIONS.get(name, "")}
        for name in ALLOWED_TOOLS
    ]


def call_tool(name: str, arguments: dict | None = None) -> dict:
    """
    统一的工具调用分发（**两条路线共用同一份分发逻辑**，避免行为不一致）

    返回：{"ok": bool, "data": Any, "msg": str}
    """
    arguments = arguments or {}

    # 1) 白名单
    if not KeyVerifier.is_tool_allowed(name):
        return {"ok": False, "data": None, "msg": f"工具 {name} 不在白名单内"}

    # 2) 分发到纯函数
    try:
        if name == "get_user_profile":
            data = profile_tool.get_user_profile(
                name=arguments.get("name"), user_id=arguments.get("user_id")
            )
        elif name == "get_medication_schedule":
            data = medication_tool.get_medication_schedule(
                user_id=arguments.get("user_id"), name=arguments.get("name")
            )
        elif name == "get_vitals":
            user_id = arguments.get("user_id")
            if not user_id:
                return {"ok": False, "data": None, "msg": "get_vitals 缺少 user_id 参数"}
            data = vitals_tool.get_vitals(
                user_id, field=arguments.get("field"), days=int(arguments.get("days") or 7)
            )
        elif name == "search_knowledge":
            query = arguments.get("query")
            if not query:
                return {"ok": False, "data": None, "msg": "search_knowledge 缺少 query 参数"}
            data = knowledge_tool.search_knowledge(
                query, top_k=int(arguments.get("top_k") or 5),
                disease_type=arguments.get("disease_type"),
            )
        elif name == "generate_report":
            user_id = arguments.get("user_id")
            if not user_id:
                return {"ok": False, "data": None, "msg": "generate_report 缺少 user_id 参数"}
            data = report_tool.generate_report(user_id, type=arguments.get("type") or "daily")
        elif name == "send_sms":
            data = notify_tool.send_sms(arguments.get("to", ""), arguments.get("content", ""))
        elif name == "send_email":
            data = notify_tool.send_email(
                arguments.get("to", ""), arguments.get("subject", ""), arguments.get("content", "")
            )
        else:                                   # 理论到不了这里（白名单已拦）
            return {"ok": False, "data": None, "msg": f"未实现的工具：{name}"}
    except Exception as e:                      # 单个工具异常不能拖垮服务
        logger.error(f"工具 {name} 执行异常：{e}")
        return {"ok": False, "data": None, "msg": f"工具 {name} 执行异常：{e}"}

    return {"ok": True, "data": data, "msg": "调用成功"}


def _dumps(data: Any) -> str:
    """把工具结果序列化成 JSON 字符串（MCP 文本协议友好）"""
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        return str(data)


# ============================================================
# 路线 A：官方 mcp SDK（FastMCP）
# ============================================================
def _build_sdk_server() -> Any:
    """
    构建 FastMCP 服务实例；未安装 mcp SDK 时返回 None（由调用方走 FastAPI 降级）

    兼容性处理：
        不同版本的 FastMCP 构造函数对 host/port 等关键字的支持不同，
        这里先按契约写法尝试，失败后退化为不带端口参数的构造，
        最终端口由 `mcp.run(transport="sse")` 的模块级 settings 决定（默认 8000）。
    """
    try:
        from mcp.server.fastmcp import FastMCP         # 可选依赖：延迟导入
    except ImportError:
        logger.warning("未安装 mcp SDK，MCP Server 降级为 FastAPI 实现")
        return None
    except Exception as e:
        logger.error(f"mcp SDK 导入异常，降级为 FastAPI 实现：{e}")
        return None

    try:
        return FastMCP("health-butler-tools", host="0.0.0.0", port=SERVER_PORT)
    except Exception as e:
        logger.warning(f"FastMCP 构造（带 host/port）失败，改用默认端口配置：{e}")
        try:
            return FastMCP("health-butler-tools")
        except Exception as e2:
            logger.error(f"FastMCP 构造失败，降级为 FastAPI 实现：{e2}")
            return None


def _register_sdk_tools(mcp: Any) -> None:
    """
    用 `@mcp.tool()` 注册 7 个工具

    说明：MCP 工具的返回值统一用 JSON 字符串（`_dumps`），
    这样 Agent 侧（mcp_tool._sdk_result_to_data）能稳定地反序列化成结构化数据。
    """

    @mcp.tool()
    def get_user_profile(name: str = "", user_id: str = "") -> str:
        """查询老人的结构化健康档案（慢病史、过敏史、紧急联系人、绑定家属）"""
        return _dumps(profile_tool.get_user_profile(name=name or None, user_id=user_id or None))

    @mcp.tool()
    def get_medication_schedule(user_id: str = "", name: str = "") -> str:
        """查询老人的用药计划与今日待服清单（含近 7 天用药依从率）"""
        return _dumps(medication_tool.get_medication_schedule(user_id=user_id or None, name=name or None))

    @mcp.tool()
    def get_vitals(user_id: str, field: str = "", days: int = 7) -> str:
        """查询老人最近若干天的生命体征时序数据与统计摘要（心率/血压/血氧/体温/血糖/睡眠）"""
        return _dumps(vitals_tool.get_vitals(user_id, field=field or None, days=days))

    @mcp.tool()
    def search_knowledge(query: str, top_k: int = 5, disease_type: str = "") -> str:
        """检索健康知识库，返回带来源、版本、页码的可溯源知识片段"""
        return _dumps(knowledge_tool.search_knowledge(query, top_k=top_k, disease_type=disease_type or None))

    @mcp.tool()
    def generate_report(user_id: str, type: str = "daily") -> str:
        """生成老人健康报告正文（type=daily 日报 / type=weekly 周报，Markdown 文本）"""
        return report_tool.generate_report(user_id, type=type)

    @mcp.tool()
    def send_sms(to: str, content: str) -> str:
        """发送短信通知给家属（模拟通道：写日志 + 落待发队列，不接真实短信网关）"""
        return _dumps(notify_tool.send_sms(to, content))

    @mcp.tool()
    def send_email(to: str, subject: str, content: str) -> str:
        """发送邮件通知给家属（真实 SMTP，配置来自 .env）"""
        return _dumps(notify_tool.send_email(to, subject, content))


# ============================================================
# 路线 B：FastAPI 降级实现
# ============================================================
def _build_fastapi_app() -> Any:
    """构建 FastAPI 降级应用（GET /healthz、GET /tools/list、POST /tools/call）"""
    try:
        from fastapi import FastAPI, Header, HTTPException
        from pydantic import BaseModel
    except ImportError as e:
        logger.error(f"fastapi / pydantic 不可用，MCP Server 无法启动：{e}")
        return None

    class ToolCallRequest(BaseModel):
        """POST /tools/call 的请求体"""
        name: str
        arguments: dict = {}

    api = FastAPI(
        title="health-butler-tools（FastAPI 降级版 MCP Server）",
        description="mcp SDK 未安装时的降级实现，语义与 MCP Server 保持一致",
        version="1.0.0",
    )

    def _auth(authorization: str) -> None:
        """统一鉴权：不通过直接 401（与 KeyVerifier 同一份逻辑）"""
        if not KeyVerifier.verify_key(authorization or ""):
            raise HTTPException(status_code=401, detail="MCP_API_KEY 校验失败")

    @api.get("/healthz")
    def healthz() -> dict:
        """健康检查（无需鉴权，供 /health 与容器探活使用）"""
        return {
            "status": "ok",
            "server": "health-butler-tools",
            "mode": "fastapi-fallback",
            "tools": sorted(ALLOWED_TOOLS),
        }

    @api.get("/tools/list")
    def list_tools(authorization: str = Header(default="")) -> dict:
        """工具清单（需鉴权）"""
        _auth(authorization)
        return {"ok": True, "data": tool_list(), "msg": "查询成功"}

    @api.post("/tools/call")
    def tools_call(payload: ToolCallRequest, authorization: str = Header(default="")) -> dict:
        """工具调用（需鉴权 + 白名单校验）"""
        _auth(authorization)
        return call_tool(payload.name, payload.arguments)

    return api


# 模块级构建：优先 SDK，其次 FastAPI 降级
mcp = _build_sdk_server()
if mcp is not None:
    try:
        _register_sdk_tools(mcp)
        logger.info(f"MCP Server 已就绪（官方 SDK / FastMCP），工具数={len(ALLOWED_TOOLS)}")
    except Exception as e:
        logger.error(f"FastMCP 工具注册失败，改用 FastAPI 降级实现：{e}")
        mcp = None

if mcp is None:
    app = _build_fastapi_app()
    if app is not None:
        logger.info(f"MCP Server 已就绪（FastAPI 降级实现），工具数={len(ALLOWED_TOOLS)}")
else:
    app = None


if __name__ == "__main__":
    # 自测/启动：优先用 MCP SDK 起 SSE 服务；未安装则用 uvicorn 起 FastAPI 降级版
    if mcp is not None:
        logger.info(f"以官方 MCP SDK 启动 SSE 服务，端口 {SERVER_PORT}")
        try:
            mcp.run(transport="sse")
        except TypeError:
            # 老版本 run() 不接受 transport 参数
            mcp.run()
    elif app is not None:
        import uvicorn

        logger.info(f"以 FastAPI 降级实现启动，端口 {SERVER_PORT}")
        uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)
    else:
        # 两条路线都不可用时，至少打印清单，方便排查（不抛异常）
        print("MCP Server 无法启动：mcp SDK 与 fastapi 均不可用")
        print("白名单工具：", sorted(ALLOWED_TOOLS))
        print("鉴权配置：MCP_API_KEY", "已配置" if settings.mcp_api_key else "未配置")
        print("当前工作目录：", os.getcwd())
