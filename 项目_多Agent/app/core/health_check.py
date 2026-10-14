"""
健康检查模块（契约第 6 节，接口锁定 / FR-15）

六项真实探测（每项都独立 try/except，单项失败只记 False，绝不影响其它项）：
    mysql      pymysql.connect + SELECT 1
    postgres   psycopg（psycopg3）或 psycopg2 连接 + SELECT 1
    redis      redis.StrictRedis(...).ping()
    influxdb   复用 app.ai.tool.influx_tool.is_available()（避免重复实现探活逻辑）
    milvus     pymilvus connections.connect / disconnect
    mcp_server GET {mcp_server_url}/healthz

降级模式判定（前端按 mode 关闭不可用功能并提示横幅，AC-26）：
    full      六项全部可用
    partial   MySQL 与 Redis 可用（核心读写链在，向量/工具链降级）
    readonly  其余情况（只读/静态页面模式）
"""
from app.core.config import settings
from app.core.logger import logger

# 单次探测超时（秒）：健康检查必须快速返回，不能拖住 /health 接口
CHECK_TIMEOUT = 2


def _check_mysql() -> bool:
    """MySQL：连接 + SELECT 1"""
    try:
        import pymysql  # 延迟导入

        conn = pymysql.connect(
            host=settings.db_host,
            port=int(settings.db_port),
            user=settings.db_user,
            password=settings.db_password,
            database=settings.db_name,
            charset="utf8mb4",
            connect_timeout=CHECK_TIMEOUT,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"MySQL 健康检查失败：{type(e).__name__}: {e}")
        return False


def _check_postgres() -> bool:
    """PostgreSQL：psycopg3 优先，其次 psycopg2"""
    try:
        try:
            import psycopg  # 延迟导入，psycopg3

            conn = psycopg.connect(settings.postgresql_url, connect_timeout=CHECK_TIMEOUT)
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                return True
            finally:
                conn.close()
        except ImportError:
            import psycopg2  # 延迟导入，psycopg2 兜底

            conn = psycopg2.connect(settings.postgresql_url, connect_timeout=CHECK_TIMEOUT)
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                return True
            finally:
                conn.close()
    except Exception as e:
        logger.error(f"PostgreSQL 健康检查失败：{type(e).__name__}: {e}")
        return False


def _check_redis() -> bool:
    """Redis：ping"""
    try:
        import redis  # 延迟导入

        client = redis.StrictRedis(**settings.redis_client_kwargs())
        return bool(client.ping())
    except Exception as e:
        logger.error(f"Redis 健康检查失败：{type(e).__name__}: {e}")
        return False


def _check_influxdb() -> bool:
    """InfluxDB：复用 influx_tool.is_available()"""
    try:
        from app.ai.tool.influx_tool import is_available

        return bool(is_available())
    except Exception as e:
        logger.error(f"InfluxDB 健康检查失败：{type(e).__name__}: {e}")
        return False


def _check_milvus() -> bool:
    """Milvus：pymilvus 连接探活后立即断开（别名独立，避免污染业务连接）"""
    try:
        from pymilvus import connections  # 延迟导入

        connections.connect(
            alias="health_check",
            host=settings.milvus_host,
            port=settings.milvus_port,
            timeout=CHECK_TIMEOUT,
        )
        try:
            return True
        finally:
            try:
                connections.disconnect("health_check")
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Milvus 健康检查失败：{type(e).__name__}: {e}")
        return False


def _check_mcp_server() -> bool:
    """MCP Server：GET /healthz"""
    try:
        import requests  # 延迟导入

        response = requests.get(f"{settings.mcp_server_url}/healthz", timeout=1)
        return bool(response.ok)
    except Exception as e:
        logger.error(f"MCP Server 健康检查失败：{type(e).__name__}: {e}")
        return False


def check_all() -> dict:
    """
    探测六项依赖，返回：
        {"mysql": bool, "postgres": bool, "redis": bool,
         "influxdb": bool, "milvus": bool, "mcp_server": bool}
    """
    status = {
        "mysql": _check_mysql(),
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "influxdb": _check_influxdb(),
        "milvus": _check_milvus(),
        "mcp_server": _check_mcp_server(),
    }
    logger.info(f"健康检查结果：{status} → 模式 {detect_mode(status)}")
    return status


def detect_mode(status: dict) -> str:
    """
    根据六项探测结果判定运行模式：
        full     全部可用
        partial  MySQL 与 Redis 都可用（核心读写链健在，向量/工具链降级）
        readonly 其它情况（只读/静态页面模式）
    """
    try:
        status = status or {}
        if all(bool(status.get(key)) for key in ("mysql", "postgres", "redis", "influxdb", "milvus", "mcp_server")):
            return "full"
        if bool(status.get("mysql")) and bool(status.get("redis")):
            return "partial"
        return "readonly"
    except Exception as e:
        logger.error(f"运行模式判定异常：{e}")
        return "readonly"


if __name__ == "__main__":
    # 自测：未启动任何依赖时应全部 False → readonly，且不抛异常
    result = check_all()
    logger.info(f"六项探测：{result}")
    logger.info("内置模式判定自测：")
    logger.info(f"  全部可用 → {detect_mode({k: True for k in result})}（应为 full）")
    logger.info(f"  只有 MySQL+Redis → {detect_mode({'mysql': True, 'redis': True})}（应为 partial）")
    logger.info(f"  全部不可用 → {detect_mode({k: False for k in result})}（应为 readonly）")
