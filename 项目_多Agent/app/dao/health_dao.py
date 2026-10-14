"""
健康档案数据访问对象（契约第 8 节，接口锁定）

职责：
    * get_profile / save_profile：老人档案（年龄/性别/慢病史/过敏史/紧急联系人）
    * list_users：需要被检测的用户集合（委托 UserDAO.list_elders，保证口径唯一）

表结构以 sql/init.sql 的 `health_profile` 为准（主键 user_id，一个用户一份档案）。
"""
from app.core.logger import logger
from app.dao.base_dao import BaseDAO
from app.dao.user_dao import UserDAO

# 允许写入的档案字段白名单（防止把任意 kwargs 拼进 SQL）
PROFILE_FIELDS = (
    "age",
    "gender",
    "height",
    "weight",
    "disease_history",
    "allergy",
    "emergency_contact",
    "emergency_phone",
    "address",
)

# 查询返回的列
PROFILE_COLUMNS = (
    "user_id, age, gender, height, weight, disease_history, allergy, "
    "emergency_contact, emergency_phone, address, update_time"
)


class HealthDAO:
    """健康档案数据访问对象（同步）"""

    @staticmethod
    def get_profile(user_id: str) -> dict | None:
        """
        查询健康档案：
        {user_id, age, gender, height, weight, disease_history, allergy,
         emergency_contact, emergency_phone, address, update_time}
        不存在返回 None。
        """
        return BaseDAO.query_one(
            f"SELECT {PROFILE_COLUMNS} FROM health_profile WHERE user_id = %s LIMIT 1",
            (str(user_id),),
        )

    @staticmethod
    def save_profile(user_id: str, **kwargs) -> bool:
        """
        新增或更新档案（upsert），返回是否成功。

        安全说明：只有 PROFILE_FIELDS 白名单内的字段会进入 SQL，
        列名来自代码常量而非用户输入，值全部走 `%s` 参数。
        """
        if not user_id:
            logger.error("save_profile 缺少 user_id")
            return False
        fields = {key: value for key, value in kwargs.items() if key in PROFILE_FIELDS}
        if not fields:
            logger.error(f"save_profile 没有可写入的合法字段：{list(kwargs.keys())}")
            return False

        columns = list(fields.keys())
        placeholders = ", ".join(["%s"] * (len(columns) + 1))
        column_text = ", ".join(columns)
        # ON DUPLICATE KEY UPDATE：存在即更新（VALUES(col) 取本次待插入的值）
        update_text = ", ".join([f"{column} = VALUES({column})" for column in columns])
        sql = (
            f"INSERT INTO health_profile (user_id, {column_text}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {update_text}"
        )
        args = (str(user_id), *[fields[column] for column in columns])
        affected = BaseDAO.execute(sql, args)
        if affected:
            logger.info(f"保存健康档案成功：user_id={user_id} 字段={columns}")
            return True
        # MySQL 在"所有值都没变化"时返回 0，此时以行是否存在作为成功判据
        exists = BaseDAO.exists(
            "SELECT user_id FROM health_profile WHERE user_id = %s LIMIT 1", (str(user_id),)
        )
        if exists:
            logger.info(f"健康档案无变化（视为成功）：user_id={user_id}")
        else:
            logger.error(f"保存健康档案失败：user_id={user_id}")
        return bool(exists)

    @staticmethod
    def list_users() -> list[str]:
        """需要被检测的用户列表（口径委托 UserDAO.list_elders）"""
        return UserDAO.list_elders()


if __name__ == "__main__":
    # 自测：未建表/未启动 MySQL 时安全返回 None / False / []
    logger.info(f"get_profile('u001') → {HealthDAO.get_profile('u001')}")
    logger.info(f"list_users() → {HealthDAO.list_users()}")
    logger.info("提示：写方法 save_profile 需要 MySQL 已就绪，且 user_id 建议先用 UserDAO 确认存在。")
