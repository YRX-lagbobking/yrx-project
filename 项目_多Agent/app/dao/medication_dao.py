"""
用药数据访问对象（契约第 8 节，接口锁定 / FR-04）

职责：
    * list_plan / list_today：用药计划（今日 = 起止日期区间内且启用中）
    * add_record / list_records：用药记录（已服 / 漏服 / 待服）
    * adherence_rate：用药依从性（已服 / 总数，0~1 的 float）

表结构以 sql/init.sql 的 `medication` / `medication_record` 为准。
注意：`list_records` 的"近 N 天"口径同时看 `create_time` 与 `plan_time`——
      前者覆盖"补录的历史记录"，后者覆盖"按计划时间查询"，避免教学演示数据被漏掉。
"""
from datetime import date, datetime, timedelta

from app.core.logger import logger
from app.dao.base_dao import BaseDAO

# 用药计划返回列（与契约 {id,drug_name,dosage,frequency,take_time,start_date,end_date,remark} 一致）
PLAN_COLUMNS = "id, drug_name, dosage, frequency, take_time, start_date, end_date, remark"

# 用药记录返回列
RECORD_COLUMNS = "id, user_id, medication_id, plan_time, actual_time, status, create_time"

# 手动登记也允许的两种状态
VALID_STATUS = ("待服", "已服", "漏服")


class MedicationDAO:
    """用药计划与用药记录数据访问对象（同步）"""

    @staticmethod
    def list_plan(user_id: str) -> list[dict]:
        """查询老人的全部生效用药计划（按 id 升序，便于稳定展示）"""
        return BaseDAO.query_dicts(
            f"SELECT {PLAN_COLUMNS} FROM medication "
            "WHERE user_id = %s AND status = 1 ORDER BY id",
            (str(user_id),),
        )

    @staticmethod
    def list_today(user_id: str) -> list[dict]:
        """查询今日生效的用药计划（起止日期区间包含今天；NULL 表示不限）"""
        today = date.today().isoformat()
        return BaseDAO.query_dicts(
            f"SELECT {PLAN_COLUMNS} FROM medication "
            "WHERE user_id = %s AND status = 1 "
            "AND (start_date IS NULL OR start_date <= %s) "
            "AND (end_date IS NULL OR end_date >= %s) "
            "ORDER BY take_time, id",
            (str(user_id), today, today),
        )

    @staticmethod
    def add_record(
        user_id: str, medication_id: int, plan_time: str, status: str = "待服"
    ) -> int:
        """
        登记一条用药记录，返回新记录 id（失败返回 0）。

        status="已服" 时同步写入 actual_time（实际服药时间），便于依从性统计与漏服分析。
        """
        status = status if status in VALID_STATUS else "待服"
        actual_time = (
            datetime.now().strftime("%Y-%m-%d %H:%M") if status == "已服" else ""
        )
        record_id = BaseDAO.execute(
            "INSERT INTO medication_record (user_id, medication_id, plan_time, actual_time, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (str(user_id), int(medication_id), str(plan_time), actual_time, status),
        )
        if not record_id:
            logger.error(
                f"用药记录写入失败：user_id={user_id} medication_id={medication_id} status={status}"
            )
        else:
            logger.info(
                f"用药记录已登记：id={record_id} user_id={user_id} status={status} plan_time={plan_time}"
            )
        return int(record_id or 0)

    @staticmethod
    def list_records(user_id: str, days: int = 7) -> list[dict]:
        """查询近 N 天的用药记录（按计划时间倒序）"""
        days = max(1, int(days or 7))
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        return BaseDAO.query_dicts(
            f"SELECT {RECORD_COLUMNS} FROM medication_record "
            "WHERE user_id = %s AND (create_time >= %s OR plan_time >= %s) "
            "ORDER BY plan_time DESC LIMIT 200",
            (str(user_id), cutoff, cutoff),
        )

    @staticmethod
    def adherence_rate(user_id: str, days: int = 7) -> float:
        """
        用药依从性 = 已服记录数 / 记录总数，返回 0~1 的 float。

        无任何记录时返回 0.0（"没有数据"与"完全漏服"对上游告警而言等价，
        调用方可结合 list_records 的条数自行区分）。
        """
        try:
            records = MedicationDAO.list_records(user_id, days)
            total = len(records)
            if total == 0:
                logger.warning(f"近 {days} 天没有用药记录，依从性按 0.0 处理：user_id={user_id}")
                return 0.0
            taken = sum(1 for record in records if str(record.get("status")) == "已服")
            rate = round(taken / total, 4)
            logger.info(f"用药依从性：user_id={user_id} 近 {days} 天 {taken}/{total} = {rate}")
            return float(rate)
        except Exception as e:  # 兜底：依从性计算异常不应打断提醒流程
            logger.error(f"计算用药依从性失败：user_id={user_id} {type(e).__name__}: {e}")
            return 0.0


if __name__ == "__main__":
    # 自测：未建表/未启动 MySQL 时安全返回 [] / 0 / 0.0
    logger.info(f"list_plan('u001') → {MedicationDAO.list_plan('u001')}")
    logger.info(f"list_today('u001') → {MedicationDAO.list_today('u001')}")
    logger.info(f"list_records('u001', 7) → {MedicationDAO.list_records('u001', 7)}")
    logger.info(f"adherence_rate('u001', 7) → {MedicationDAO.adherence_rate('u001', 7)}")
    logger.info("提示：写方法 add_record 需要 MySQL 已就绪；medication_id 应取自 list_plan 的 id。")
