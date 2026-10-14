"""
系统路由（健康检查 / 降级模式感知）

职责：
    GET /health：探测六个依赖（MySQL / PostgreSQL / Redis / InfluxDB / Milvus / MCP Server），
    返回统一状态、降级模式与配置快照，供前端按模式关闭不可用功能并提示横幅（FR-15 / AC-26）。

设计说明：
    1. 本接口**本身不能失败**：任何异常都降级为 critical + readonly，绝不 500（否则前端无法判断服务状态）；
    2. 不存在鉴权依赖（契约第 18 节：/health 免鉴权），便于容器编排探针直接调用；
    3. 只导出非敏感配置（settings.as_dict()），Token / 密码永不出接口。
"""
from fastapi import APIRouter

from app.core.config import settings
from app.core.logger import logger

system_router = APIRouter()

# 六项依赖的展示名（与 check_all 返回的键名严格对应）
DEPENDENCY_LABELS = {
    "mysql": "MySQL（业务数据）",
    "postgres": "PostgreSQL（摘要记忆/Checkpointer）",
    "redis": "Redis（窗口记忆/会话/锁）",
    "influxdb": "InfluxDB（生命体征时序）",
    "milvus": "Milvus（知识库向量）",
    "mcp_server": "MCP Server（工具服务）",
}


def _config_snapshot() -> dict:
    """配置快照（失败也不要影响 /health 主流程）"""
    try:
        return settings.as_dict()
    except Exception as e:
        logger.error(f"配置快照导出失败：{e}")
        return {}


@system_router.get("/health")
async def health() -> dict:
    """
    服务健康与降级模式

    返回：
        {
          "status": "healthy" | "degraded" | "critical",
          "mode":   "full" | "partial" | "readonly",
          "detail": {"mysql": true, ...},
          "config": {...}
        }
    判定规则：全部依赖可用 → healthy/full；部分不可用 → degraded/partial；全部不可用 → critical/readonly。
    """
    detail: dict = {}
    status: dict = {}
    mode = "full"

    # 1. 探测依赖（check_all 内部已逐项 try/except，这里再兜一层）
    try:
        from app.core.health_check import check_all

        status = check_all() or {}
        # bool 值只 json 序列化安全，统一转换（若上游返回的是字符串状态同样兼容）
        detail = {k: (v if isinstance(v, bool) else bool(v)) for k, v in status.items()}
    except Exception as e:
        logger.error(f"健康检查执行失败（降级为 critical）：{e}")
        detail = {}
        status = {}

    # 2. 计算整体状态
    try:
        total = len(detail)
        ok_count = sum(1 for v in detail.values() if v)
        if total == 0 or ok_count == 0:
            overall = "critical"
        elif ok_count == total:
            overall = "healthy"
        else:
            overall = "degraded"
    except Exception as e:
        logger.error(f"健康状态汇总失败（降级为 critical）：{e}")
        overall = "critical"

    # 3. 降级模式（detect_mode 入参按契约传状态字典）
    try:
        from app.core.health_check import detect_mode

        mode = detect_mode(status) or mode
    except Exception as e:
        logger.error(f"降级模式判定失败（按状态推断）：{e}")
        mode = {"healthy": "full", "degraded": "partial", "critical": "readonly"}.get(overall, "readonly")

    # 4. 关键依赖缺失时补充提示，便于前端展示具体原因
    unavailable = [DEPENDENCY_LABELS.get(k, k) for k, v in detail.items() if not v]
    if unavailable:
        logger.warning(f"以下依赖不可用：{'、'.join(unavailable)}")

    return {
        "status": overall,
        "mode": mode,
        "detail": detail,
        "config": _config_snapshot(),
    }


# ---------------- 调用示例 ----------------
# curl http://localhost:8000/health
# → {"status":"degraded","mode":"partial","detail":{"mysql":true,"milvus":false,...},"config":{...}}
