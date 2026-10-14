"""
MySQL 只读查询工具（LangChain Tool）

职责：
    给大模型提供一个"能查业务库、但绝对改不了数据"的受限 SQL 通道。
    典型用途：查询用户信息、用药计划、告警记录、知识文档等结构化数据。

安全设计（契约第 9 节 / 需求 FR-11，三道防线）：
    1. 白名单：SQL 必须以 SELECT / SHOW / DESC / DESCRIBE / EXPLAIN / WITH 开头，否则拒绝；
    2. 黑名单：语句中出现 DELETE / UPDATE / INSERT / DROP / CREATE / ALTER / TRUNCATE /
       REPLACE / GRANT / REVOKE 等写关键字（含注释包裹）一律拒绝；
    3. 单语句：拒绝一个入参里塞多条 SQL（用分号分隔），防止 `SELECT 1; DROP TABLE x`。

与 exam_0914 的差异：
    - 配置改为统一走 `settings`（禁止业务模块散落 os.getenv）；
    - 连接与游标使用上下文管理，异常统一返回字符串（契约第 0 节：工具永不抛异常给上层）；
    - SQL 关键字校验改用 `re` 按"单词边界"匹配，避免 `created_at` 被误判为 CREATE。
"""
import re

from langchain.tools import tool
from pymysql import connect, cursors

from app.ai.tool.schema.mysql_schema import MySQLSchema
from app.core.config import settings
from app.core.logger import logger

# 只读语句白名单（首关键字）
ALLOWED_STARTS = ("SELECT", "SHOW", "DESC", "DESCRIBE", "EXPLAIN", "WITH")

# 写操作黑名单（按单词边界匹配，避免误伤 created_at / update_time 之类字段名）
FORBIDDEN_KEYWORDS = (
    "DELETE",
    "UPDATE",
    "INSERT",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "REPLACE",
    "GRANT",
    "REVOKE",
    "RENAME",
    "LOAD",
    "CALL",
)

# 单次查询最多返回的行数（防止大模型一句 SELECT * 把内存打爆）
MAX_ROWS = 200


def _clean_sql(sql: str) -> str:
    """去掉首尾空白与末尾分号"""
    return (sql or "").strip().rstrip(";").strip()


def check_sql_safe(sql: str) -> tuple[bool, str]:
    """
    校验 SQL 是否安全（只读）。

    返回 (是否通过, 拒绝原因)；通过时原因为空串。
    独立成函数是为了可单测——不依赖数据库连接就能验证安全策略。
    """
    cleaned = _clean_sql(sql)
    if not cleaned:
        return False, "SQL 语句不能为空"

    # 去掉字符串字面量与注释后再做关键字匹配，避免 'drop' 之类内容误判
    probe = re.sub(r"'[^']*'|\"[^\"]*\"", " ", cleaned)
    probe = re.sub(r"--[^\n]*|#[^\n]*|/\*.*?\*/", " ", probe, flags=re.S)
    upper = probe.upper()

    # 1) 只读白名单
    if not upper.startswith(ALLOWED_STARTS):
        return False, f"仅允许 {'/'.join(ALLOWED_STARTS)} 开头的只读查询语句"

    # 2) 写操作黑名单（单词边界匹配）
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper):
            return False, f"禁止执行包含 {keyword} 的写操作语句"

    # 3) 禁止多语句
    if ";" in cleaned:
        return False, "禁止一次提交多条 SQL 语句"

    return True, ""


def execute_readonly_sql(sql: str) -> str:
    """
    执行只读 SQL 并返回字符串结果（供 LangChain Tool 与 MCP 复用）。

    永不抛异常：参数未配置 / 校验不通过 / 数据库异常一律返回中文提示字符串。
    """
    ok, reason = check_sql_safe(sql)
    if not ok:
        logger.warning(f"mysql_tool 拒绝执行：{reason} | SQL={sql!r}")
        return f"SQL 被安全策略拦截：{reason}"

    if not settings.db_host or not settings.db_user or not settings.db_name:
        return "数据库连接参数未配置，无法执行查询"

    con = None
    cursor = None
    try:
        con = connect(
            host=settings.db_host,
            port=int(settings.db_port),
            user=settings.db_user,
            password=settings.db_password,
            database=settings.db_name,
            charset="utf8mb4",          # 中文编码设置（utf8mb4 兼容 emoji）
            cursorclass=cursors.Cursor,
            connect_timeout=5,
        )
        cursor = con.cursor()
        cursor.execute(_clean_sql(sql))
        rows = cursor.fetchmany(MAX_ROWS)
        if not rows:
            return "查询成功，但没有匹配的数据"
        return str(rows)
    except Exception as e:
        logger.error(f"mysql_tool 执行异常：{e}")
        return f"查询失败：{e}"
    finally:
        # 释放资源：游标先关、连接后关
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if con:
                con.close()
        except Exception:
            pass


@tool(args_schema=MySQLSchema)
def mysql_tool(sql: str) -> str:
    """
    执行只读 SQL 查询 MySQL 业务库（健康管家系统）

    可用表（节选，字段以 sql/init.sql 为准）：
        user_info         用户表：user_id 用户编号、user_name 用户名、email 邮箱、phone 手机号、role 角色
        health_profile    健康档案：user_id、age 年龄、gender 性别、disease_history 慢病史、
                          allergy 过敏史、emergency_contact 紧急联系人、emergency_phone 紧急联系电话
        medication       用药计划：id、user_id、drug_name 药名、dosage 剂量、frequency 频次、
                          take_time 服用时间、start_date、end_date、remark
        medication_record 用药记录：user_id、medication_id、plan_time、status（已服/漏服/待服）
        alert_record      告警记录：user_id、alert_type、level 等级、value、description、source
        knowledge_doc     知识文档：doc_id、title、source、version、disease_type、page、content

    规则：
        - 只允许 SELECT / SHOW / DESC / EXPLAIN / WITH 开头的只读语句；
        - 禁止 DELETE、UPDATE、INSERT、DROP、CREATE、ALTER 等任何写操作；
        - 一次只能提交一条 SQL，最多返回 200 行。
    """
    return execute_readonly_sql(sql)


if __name__ == "__main__":
    # 自测：不需要数据库即可验证安全策略；有数据库时会真实执行第一条查询
    test_sqls = [
        "SELECT * FROM user_info LIMIT 1",
        "DELETE FROM user_info",
        "SELECT 1; DROP TABLE user_info",
        "UPDATE user_info SET role='admin'",
        "SELECT created_at, updated_at FROM alert_record",
    ]
    for s in test_sqls:
        passed, why = check_sql_safe(s)
        print(f"{'通过' if passed else '拦截'} | {s} | {why}")
    print("实际执行：", mysql_tool.invoke({"sql": "SELECT user_id FROM user_info LIMIT 1"}))
