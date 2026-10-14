"""
DAO 基类（契约第 8 节，接口锁定）

职责：唯一与 MySQL 打交道的地方，向上提供四个原语：
    query(sql, args)         → list[tuple]
    query_one(sql, args)     → dict | None
    query_dicts(sql, args)   → list[dict]（用 cursor.description 把列名映射成 key）
    execute(sql, args)       → int（自增主键 id / 影响行数；失败返回 0）

硬性约定：
    1. **全部参数化查询**（`%s` 占位符 + 元组参数），任何情况下都不拼接 SQL 值；
    2. 连接配置一律取自 settings（禁止 os.getenv 散落）；
    3. 异常捕获后 logger.error 并返回 [] / 0，**永不向上抛异常**（契约第 0 节"返回风格"）；
    4. finally 关闭连接——教学/演示规模下"每次调用新建连接"，生产应改为连接池
       （pymysql + DBUtils.PooledDB 或 SQLAlchemy engine）。
"""
from app.core.config import settings
from app.core.logger import logger


class BaseDAO:
    """MySQL 访问基类（全部为静态方法，直接 BaseDAO.query(...) 调用）"""

    @staticmethod
    def get_connection():
        """
        建立一个 MySQL 连接；失败返回 None。

        pymysql 采用**函数内延迟导入**：未安装时模块仍可导入，
        调用方拿到 None 后按"依赖不可用"降级。
        """
        try:
            import pymysql  # 延迟导入

            return pymysql.connect(
                host=settings.db_host,
                port=int(settings.db_port),
                user=settings.db_user,
                password=settings.db_password,
                database=settings.db_name,
                charset="utf8mb4",  # 全库 utf8mb4，支持 emoji 与中文
                autocommit=True,  # 简化教学流程：写操作立即生效
                connect_timeout=5,
            )
        except ImportError as e:
            logger.error(f"未安装 pymysql，MySQL 功能全部降级：{e}")
            return None
        except Exception as e:
            logger.error(
                f"MySQL 连接失败（{settings.db_host}:{settings.db_port}/{settings.db_name}）："
                f"{type(e).__name__}: {e}"
            )
            return None

    @staticmethod
    def _safe_args(args) -> tuple:
        """参数规范化：None → 空元组；list → tuple（pymysql 只接受 tuple/list）"""
        if args is None:
            return ()
        if isinstance(args, tuple):
            return args
        if isinstance(args, list):
            return tuple(args)
        return (args,)

    @staticmethod
    def query(sql: str, args: tuple = ()) -> list[tuple]:
        """
        执行查询，返回原始行列表 list[tuple]；任何异常返回 []。
        日志只记录 SQL 文本与参数个数（参数里可能含手机号等隐私，NFR-03 要求脱敏）。
        """
        conn = BaseDAO.get_connection()
        if conn is None:
            return []
        safe_args = BaseDAO._safe_args(args)
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, safe_args)
                rows = cursor.fetchall()
            return list(rows or [])
        except Exception as e:
            logger.error(
                f"SQL 查询失败：{type(e).__name__}: {e} | SQL={sql[:200]} | 参数个数={len(safe_args)}"
            )
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def query_dicts(sql: str, args: tuple = ()) -> list[dict]:
        """
        执行查询并把每行映射成 dict（key 取 cursor.description 的列名）。
        """
        conn = BaseDAO.get_connection()
        if conn is None:
            return []
        safe_args = BaseDAO._safe_args(args)
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, safe_args)
                rows = cursor.fetchall() or []
                # cursor.description: ((name, type_code, ...), ...)
                columns = [item[0] for item in (cursor.description or [])]
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error(
                f"SQL 查询失败：{type(e).__name__}: {e} | SQL={sql[:200]} | 参数个数={len(safe_args)}"
            )
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def query_one(sql: str, args: tuple = ()) -> dict | None:
        """执行查询并返回第一行 dict；无结果或异常返回 None"""
        rows = BaseDAO.query_dicts(sql, args)
        return rows[0] if rows else None

    @staticmethod
    def execute(sql: str, args: tuple = ()) -> int:
        """
        执行写操作，返回自增主键 id（INSERT）或影响行数；异常返回 0。

        注意：MySQL 在 `ON DUPLICATE KEY UPDATE` 值未变化时返回 0，
        调用方若需区分"未变更"与"失败"，可再查一次存在性（见 HealthDAO.save_profile）。
        """
        conn = BaseDAO.get_connection()
        if conn is None:
            return 0
        safe_args = BaseDAO._safe_args(args)
        try:
            with conn.cursor() as cursor:
                affected = cursor.execute(sql, safe_args)
                lastrowid = cursor.lastrowid
            conn.commit()
            return int(lastrowid or affected or 0)
        except Exception as e:
            logger.error(
                f"SQL 执行失败：{type(e).__name__}: {e} | SQL={sql[:200]} | 参数个数={len(safe_args)}"
            )
            return 0
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def exists(sql: str, args: tuple = ()) -> bool:
        """存在性判断（供"值未变化返回 0"的写操作做成功性复核）"""
        return BaseDAO.query_one(sql, args) is not None


if __name__ == "__main__":
    # 自测：未安装 pymysql 或 MySQL 未启动时也必须安全返回（[] / 0 / None）
    logger.info(f"MySQL 配置：{settings.db_host}:{settings.db_port}/{settings.db_name}")
    logger.info(f"query → {BaseDAO.query('SELECT 1')}")
    logger.info(f"query_dicts → {BaseDAO.query_dicts('SELECT 1 AS ok')}")
    logger.info(f"query_one → {BaseDAO.query_one('SELECT 1 AS ok')}")
    logger.info(f"execute → {BaseDAO.execute('SELECT 1')}（只读语句返回影响行数或 0）")
    logger.info("提示：真实数据需要先执行 sql/init.sql 建库建表，并确保 MySQL 已启动。")
