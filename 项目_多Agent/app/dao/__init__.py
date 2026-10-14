"""
数据访问包（同步 + 参数化查询，契约第 8 节）。

    base_dao.py       连接与通用 query/execute（唯一与 pymysql 打交道的地方）
    user_dao.py       用户 / 家属绑定 / 登录校验
    health_dao.py     健康档案
    medication_dao.py 用药计划与用药记录（依从性）
    alert_dao.py      告警记录与统计

约定：
    * 全部同步方法（契约第 0 节：DAO 为同步层），异步节点里调用属于教学规模可接受的做法；
    * SQL 一律 `%s` 参数化，禁止字符串拼接（NFR-03）；
    * 异常不外抛：查询失败返回 []，写入失败返回 0。
"""
from app.dao.alert_dao import AlertDAO
from app.dao.base_dao import BaseDAO
from app.dao.health_dao import HealthDAO
from app.dao.medication_dao import MedicationDAO
from app.dao.user_dao import UserDAO

__all__ = ["BaseDAO", "UserDAO", "HealthDAO", "MedicationDAO", "AlertDAO"]
