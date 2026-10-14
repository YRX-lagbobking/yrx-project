"""
告警数据访问对象（契约第 8 节，接口锁定 / FR-07）

职责：
    * save：写入告警（**只有自动检测/检测闭环会写**，纯计算层 detect_anomaly 不写，AC-10）
    * list_by_user / list_by_elders：本人或绑定老人的告警列表（RBAC 数据权限）
    * confirm：家属确认告警（闭环）
    * count_today / summary_days：按天聚合统计（日报/周报图表）

表结构以 sql/init.sql 的 `alert_record` 为准（含 metric 与 notify_result 留痕列）。
"""
from datetime import date, timedelta

from app.core.logger import logger
from app.dao.base_dao import BaseDAO

# 告警列表返回列
ALERT_COLUMNS = (
    "id, user_id, alert_type, level, value, metric, description, source, "
    "status, notify_result, create_time, confirm_time"
)

# 异常 type → 指标名（写入 metric 列，便于按指标聚合统计）
METRIC_BY_TYPE = {
    "heart_rate_high": "heart_rate",
    "heart_rate_low": "heart_rate",
    "blood_pressure_high": "blood_pressure_sys",
    "blood_pressure_low": "blood_pressure_sys",
    "blood_pressure_dia_high": "blood_pressure_dia",
    "spo2_low": "spo2",
    "temperature_high": "temperature",
    "temperature_low": "temperature",
    "glucose_high": "glucose",
    "glucose_low": "glucose",
    "trend_heart_rate": "heart_rate",
    "trend_blood_pressure": "blood_pressure_sys",
    "trend_spo2": "spo2",
}


def _metric_of(alert_type: str) -> str:
    """由 alert_type 推导指标名：查表优先，其次剥离 trend_/outlier_ 前缀"""
    text = str(alert_type or "")
    if text in METRIC_BY_TYPE:
        return METRIC_BY_TYPE[text]
    for prefix in ("outlier_", "trend_"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return ""


def _to_text(value) -> str:
    """把 value 统一成字符串（表字段是 VARCHAR(32)）：整数不带小数点"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float) and abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return str(value)


class AlertDAO:
    """告警记录数据访问对象（同步）"""

    @staticmethod
    def save(
        user_id,
        alert_type,
        level,
        value,
        description,
        source: str = "auto",
    ) -> int:
        """
        写入一条告警记录，返回自增主键 id（失败返回 0）。

        参数：
            user_id     老人 user_id
            alert_type  异常类型（heart_rate_high / trend_blood_pressure / outlier_spo2 ...）
            level       高危 / 中危 / 低危
            value       触发数值（可为 int/float/str）
            description 中文描述
            source      auto 自动检测 / manual 手动检测
        """
        metric = _metric_of(alert_type)
        alert_id = BaseDAO.execute(
            "INSERT INTO alert_record "
            "(user_id, alert_type, level, value, metric, description, source, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                str(user_id),
                str(alert_type or ""),
                str(level or "低危"),
                _to_text(value),
                metric,
                str(description or ""),
                str(source or "auto"),
                "待确认",
            ),
        )
        if not alert_id:
            logger.error(f"告警写入失败：user_id={user_id} type={alert_type} level={level}")
        else:
            logger.warning(
                f"告警已记录：id={alert_id} user_id={user_id} type={alert_type} "
                f"level={level} source={source}"
            )
        return int(alert_id or 0)

    @staticmethod
    def list_by_user(user_id: str, limit: int = 20) -> list[dict]:
        """查询某个用户的告警列表（按时间倒序）"""
        return BaseDAO.query_dicts(
            f"SELECT {ALERT_COLUMNS} FROM alert_record "
            "WHERE user_id = %s ORDER BY create_time DESC, id DESC LIMIT %s",
            (str(user_id), int(limit or 20)),
        )

    @staticmethod
    def list_by_elders(elder_ids: list[str], limit: int = 20) -> list[dict]:
        """
        查询多个老人（家属绑定的老人集合）的告警列表。
        入参为空时直接返回 []（避免生成 `IN ()` 这种非法 SQL）。
        """
        ids = [str(item) for item in (elder_ids or []) if item]
        if not ids:
            return []
        placeholders = ", ".join(["%s"] * len(ids))  # 只生成占位符，值仍走参数化
        args = tuple(ids) + (int(limit or 20),)
        return BaseDAO.query_dicts(
            f"SELECT {ALERT_COLUMNS} FROM alert_record "
            f"WHERE user_id IN ({placeholders}) ORDER BY create_time DESC, id DESC LIMIT %s",
            args,
        )

    @staticmethod
    def confirm(alert_id: int, status: str = "已确认") -> bool:
        """
        家属确认告警（闭环）：更新 status 并记录 confirm_time。
        返回是否成功。
        """
        affected = BaseDAO.execute(
            "UPDATE alert_record SET status = %s, confirm_time = NOW() WHERE id = %s",
            (str(status or "已确认"), int(alert_id)),
        )
        if affected:
            logger.info(f"告警已确认：id={alert_id} status={status}")
            return True
        # 幂等场景：重复确认时 MySQL 影响行数为 0，用存在性复核避免误判失败
        exists = BaseDAO.exists(
            "SELECT id FROM alert_record WHERE id = %s LIMIT 1", (int(alert_id),)
        )
        if not exists:
            logger.error(f"确认告警失败：id={alert_id} 记录不存在")
        return bool(exists)

    @staticmethod
    def update_notify_result(alert_id: int, notify_result: str) -> bool:
        """
        回写通知结果（NFR-02"全程留痕"）：短信/邮件/WS 通道的发送结论。
        """
        affected = BaseDAO.execute(
            "UPDATE alert_record SET notify_result = %s WHERE id = %s",
            (str(notify_result or "")[:256], int(alert_id)),
        )
        if not affected:
            logger.warning(f"回写通知结果失败（可能记录不存在）：id={alert_id}")
        return bool(affected)

    @staticmethod
    def count_today(user_id: str) -> int:
        """统计今天的告警条数"""
        rows = BaseDAO.query(
            "SELECT COUNT(*) FROM alert_record "
            "WHERE user_id = %s AND DATE(create_time) = CURDATE()",
            (str(user_id),),
        )
        if not rows or not rows[0]:
            return 0
        try:
            return int(rows[0][0] or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def summary_days(user_id: str, days: int = 7) -> list[dict]:
        """
        按天 + 级别聚合告警：[{date, level, count}]（按日期倒序）。
        """
        days = max(1, int(days or 7))
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        rows = BaseDAO.query_dicts(
            "SELECT DATE(create_time) AS day, level, COUNT(*) AS total "
            "FROM alert_record WHERE user_id = %s AND create_time >= %s "
            "GROUP BY day, level ORDER BY day DESC, level",
            (str(user_id), cutoff),
        )
        summary = []
        for row in rows:
            summary.append(
                {
                    "date": str(row.get("day") or ""),
                    "level": str(row.get("level") or ""),
                    "count": int(row.get("total") or 0),
                }
            )
        return summary


if __name__ == "__main__":
    # 自测：未建表/未启动 MySQL 时安全返回 0 / [] / False
    logger.info(f"save(演示) → {AlertDAO.save('u001', 'heart_rate_high', '高危', 140, '心率达到 140 次/分，超过安全范围')}（需要 MySQL）")
    logger.info(f"list_by_user('u001') → {AlertDAO.list_by_user('u001')}")
    logger.info(f"list_by_elders(['u001', 'u002']) → {AlertDAO.list_by_elders(['u001', 'u002'])}")
    logger.info(f"list_by_elders([]) → {AlertDAO.list_by_elders([])}（应为 []）")
    logger.info(f"count_today('u001') → {AlertDAO.count_today('u001')}")
    logger.info(f"summary_days('u001', 7) → {AlertDAO.summary_days('u001', 7)}")
    logger.info(f"confirm(1) → {AlertDAO.confirm(1)}（需要 MySQL 且 id=1 存在）")
    logger.info("提示：请先执行 sql/init.sql；本表的 id 为自增主键，save 返回的即为告警 id。")
