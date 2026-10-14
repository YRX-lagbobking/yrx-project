"""
用户数据访问对象（契约第 8 节，接口锁定）

职责：
    * 用户基础 CRUD（get_by_id / get_by_name / get_by_email / create）
    * 登录校验（verify_login：用 app.core.security.verify_password 比对哈希）
    * 家属-老人绑定关系（bind_family / list_family / list_bound_elders）
    * 通知与检测所需的用户集合（get_emergency_contact / list_elders）

安全说明（NFR-03 / FR-11）：
    * `user` 是 MySQL 保留字，全部用反引号包裹；
    * 对外的用户字典**不返回 password 哈希**（只有内部登录查询会取 password 列），
      避免路由层把哈希透传给前端；
    * 所有 SQL 均为 `%s` 参数化，杜绝拼接注入。
"""
import uuid

from app.core.config import settings
from app.core.logger import logger
from app.core.security import hash_password, mask_email, mask_phone, verify_password
from app.dao.base_dao import BaseDAO

# 对外返回的用户列（不含 password）
PUBLIC_COLUMNS = "user_id, user_name, email, phone, role, status, create_time"

# 角色 → user_id 前缀（与 sql/init.sql 的 u001 / f001 / admin 命名保持一致）
ROLE_PREFIX = {"elder": "u", "family": "f", "admin": "a"}


class UserDAO:
    """用户与家庭关系数据访问对象（同步）"""

    @staticmethod
    def get_by_id(user_id: str) -> dict | None:
        """按 user_id 精确查询用户（不含密码）"""
        return BaseDAO.query_one(
            f"SELECT {PUBLIC_COLUMNS} FROM `user` WHERE user_id = %s LIMIT 1",
            (str(user_id),),
        )

    @staticmethod
    def get_by_name(user_name: str) -> dict | None:
        """按登录名查询用户（不含密码；登录校验请用 verify_login）"""
        return BaseDAO.query_one(
            f"SELECT {PUBLIC_COLUMNS} FROM `user` WHERE user_name = %s LIMIT 1",
            (str(user_name),),
        )

    @staticmethod
    def get_by_email(email: str) -> dict | None:
        """按邮箱查询用户（不含密码）"""
        return BaseDAO.query_one(
            f"SELECT {PUBLIC_COLUMNS} FROM `user` WHERE email = %s LIMIT 1",
            (str(email),),
        )

    @staticmethod
    def generate_user_id(role: str = "elder") -> str:
        """
        生成新的 user_id：`u` / `f` / `a` + 数字序号（与 init.sql 的 u001、f001 对齐）。

        规则：取同前缀的最大数字后缀 +1（不足 3 位补零）；解析失败时用随机后缀兜底。
        """
        prefix = ROLE_PREFIX.get(str(role).lower(), "u")
        rows = BaseDAO.query(
            "SELECT user_id FROM `user` WHERE user_id LIKE %s",
            (f"{prefix}%",),
        )
        max_number = 0
        for row in rows:
            text = str(row[0] or "")
            suffix = text[len(prefix):]
            if suffix.isdigit():
                max_number = max(max_number, int(suffix))
        if max_number:
            return f"{prefix}{max_number + 1:03d}"
        # 兜底：库里还没有同前缀用户时从 001 开始；异常情况用随机后缀保证唯一
        return f"{prefix}001" if rows == [] else f"{prefix}{uuid.uuid4().hex[:6]}"

    @staticmethod
    def create_user(
        user_name: str, password: str, email: str = "", phone: str = "", role: str = "elder"
    ) -> str | None:
        """
        创建用户，返回新 user_id；失败返回 None。

        这是 `create()` 的"拿得到主键"版本（本表主键是 VARCHAR，不是自增列）。
        """
        user_id = UserDAO.generate_user_id(role)
        affected = BaseDAO.execute(
            "INSERT INTO `user` (user_id, user_name, password, email, phone, role) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                user_id,
                str(user_name),
                hash_password(password),  # 只存哈希，不存明文
                str(email or ""),
                str(phone or ""),
                str(role or "elder"),
            ),
        )
        if not affected:
            logger.error(f"创建用户失败：user_name={user_name}（可能重名或数据库不可用）")
            return None
        logger.info(
            f"创建用户成功：user_id={user_id} user_name={user_name} role={role} "
            f"email={mask_email(email)} phone={mask_phone(phone)}"
        )
        return user_id

    @staticmethod
    def create(
        user_name: str, password: str, email: str = "", phone: str = "", role: str = "elder"
    ) -> int:
        """
        创建用户，返回影响行数（1 = 成功，0 = 失败）。

        ⚠️ 本表主键 `user_id` 是 VARCHAR（见 sql/init.sql），没有自增 id，
        因此这里返回的是"是否写入成功"；需要新 user_id 请用 `create_user()`。
        """
        user_id = UserDAO.create_user(user_name, password, email, phone, role)
        return 1 if user_id else 0

    @staticmethod
    def verify_login(user_name: str, password: str) -> dict | None:
        """
        登录校验：查哈希 → `verify_password` 比对 → 返回用户信息（不含密码）。

        停用（status=0）的账号一律视为校验失败。
        """
        row = BaseDAO.query_one(
            "SELECT user_id, user_name, password, email, phone, role, status "
            "FROM `user` WHERE user_name = %s LIMIT 1",
            (str(user_name),),
        )
        if not row:
            logger.warning(f"登录失败：用户不存在 user_name={user_name}")
            return None
        # status 是 TINYINT(1)；非数值时不抛异常，一律按"账号不可用"处理
        status = row.get("status")
        try:
            active = int(status) == 1
        except (TypeError, ValueError):
            active = str(status).lower() in ("1", "true")
        if not active:
            logger.warning(f"登录失败：账号已停用 user_name={user_name}")
            return None
        if not verify_password(password, row.get("password") or ""):
            logger.warning(f"登录失败：密码错误 user_name={user_name}")
            return None
        row.pop("password", None)  # 绝不把哈希返回给上层
        logger.info(f"登录成功：user_id={row.get('user_id')} role={row.get('role')}")
        return row

    @staticmethod
    def bind_family(elder_id: str, family_id: str) -> bool:
        """
        绑定家属与老人（唯一键 elder_id+family_id，重复绑定不报错）。
        返回 True 表示本次绑定成功。
        """
        affected = BaseDAO.execute(
            "INSERT IGNORE INTO elder_family (elder_id, family_id) VALUES (%s, %s)",
            (str(elder_id), str(family_id)),
        )
        if affected:
            logger.info(f"绑定成功：elder_id={elder_id} family_id={family_id}")
            return True
        # 已经绑定过 / 数据库不可用
        exists = BaseDAO.exists(
            "SELECT id FROM elder_family WHERE elder_id = %s AND family_id = %s LIMIT 1",
            (str(elder_id), str(family_id)),
        )
        if exists:
            logger.info(f"绑定关系已存在：elder_id={elder_id} family_id={family_id}")
        else:
            logger.error(f"绑定失败：elder_id={elder_id} family_id={family_id}")
        return bool(exists)

    @staticmethod
    def list_family(elder_id: str) -> list[dict]:
        """查询老人的家属列表：[{user_id,user_name,email,phone,role}]"""
        return BaseDAO.query_dicts(
            "SELECT u.user_id, u.user_name, u.email, u.phone, u.role "
            "FROM elder_family AS ef "
            "JOIN `user` AS u ON u.user_id = ef.family_id "
            "WHERE ef.elder_id = %s ORDER BY ef.create_time",
            (str(elder_id),),
        )

    @staticmethod
    def list_family_contacts(elder_id: str) -> list[dict]:
        """
        查询老人的家属联系方式（**邮箱取自 `elder_family` 表**，即"老人子女的收件邮箱"）。

        与 list_family 的区别：
            · list_family 取 `user` 表里家属账号自己的邮箱；
            · 本方法优先取 `elder_family.email`（老人为这位子女登记的收件邮箱），为空时才
              退回家属账号邮箱，并附带 `relation`（儿子/女儿），便于按老人说的关系选人。

        返回：[{family_id, relation, email, email_from, user_name, phone}]
            email_from: elder_family（绑定表登记的邮箱） / user（退回账号邮箱） / ""（都没有）
        """
        rows = BaseDAO.query_dicts(
            "SELECT ef.family_id, ef.relation, ef.email AS family_email, "
            "       u.user_name, u.email AS user_email, u.phone "
            "FROM elder_family AS ef "
            "LEFT JOIN `user` AS u ON u.user_id = ef.family_id "
            "WHERE ef.elder_id = %s ORDER BY ef.create_time",
            (str(elder_id),),
        )
        contacts: list[dict] = []
        for row in rows or []:
            family_email = str(row.get("family_email") or "").strip()
            user_email = str(row.get("user_email") or "").strip()
            contacts.append({
                "family_id": row.get("family_id"),
                "relation": row.get("relation") or "",
                "email": family_email or user_email,
                "email_from": "elder_family" if family_email else ("user" if user_email else ""),
                "user_name": row.get("user_name") or "",
                "phone": row.get("phone") or "",
            })
        return contacts

    @staticmethod
    def list_bound_elders(family_id: str) -> list[str]:
        """查询家属绑定的老人 user_id 列表（RBAC 数据权限的判断依据）"""
        rows = BaseDAO.query(
            "SELECT elder_id FROM elder_family WHERE family_id = %s ORDER BY create_time",
            (str(family_id),),
        )
        return [str(row[0]) for row in rows if row and row[0]]

    @staticmethod
    def get_emergency_contact(elder_id: str) -> dict | None:
        """
        查询老人的紧急联系人：{name, phone, email}。

        姓名/电话取 health_profile，邮箱优先取绑定家属的邮箱（用于邮件通道降级）。
        三者全空返回 None。
        """
        profile = BaseDAO.query_one(
            "SELECT emergency_contact, emergency_phone FROM health_profile WHERE user_id = %s LIMIT 1",
            (str(elder_id),),
        )
        family = UserDAO.list_family(elder_id)
        name = str((profile or {}).get("emergency_contact") or "").strip()
        phone = str((profile or {}).get("emergency_phone") or "").strip()
        email = ""
        for member in family:
            if member.get("email"):
                email = str(member["email"]).strip()
                break
        if not name and family:
            name = str(family[0].get("user_name") or "").strip()
        if not phone and family:
            phone = str(family[0].get("phone") or "").strip()
        if not (name or phone or email):
            return None
        return {"name": name, "phone": phone, "email": email}

    @staticmethod
    def list_elders() -> list[str]:
        """所有在用的老人 user_id（自动检测与数据模拟器的输入集合）"""
        rows = BaseDAO.query(
            "SELECT user_id FROM `user` WHERE role = %s AND status = 1 ORDER BY user_id",
            ("elder",),
        )
        return [str(row[0]) for row in rows if row and row[0]]


if __name__ == "__main__":
    # 自测：未建表/未启动 MySQL 时全部安全降级（None / 0 / []）
    logger.info(f"数据库：{settings.db_host}:{settings.db_port}/{settings.db_name}")
    logger.info(f"list_elders() → {UserDAO.list_elders()}")
    logger.info(f"get_by_name('zhangsan') → {UserDAO.get_by_name('zhangsan')}")
    logger.info(f"verify_login('zhangsan', '123456') → {UserDAO.verify_login('zhangsan', '123456')}")
    logger.info(f"list_family('u001') → {UserDAO.list_family('u001')}")
    logger.info(f"list_bound_elders('f001') → {UserDAO.list_bound_elders('f001')}")
    logger.info(f"get_emergency_contact('u001') → {UserDAO.get_emergency_contact('u001')}")
    logger.info(f"generate_user_id('elder') → {UserDAO.generate_user_id('elder')}")
    logger.info("提示：请先执行 sql/init.sql 初始化数据库并确认 MySQL 已启动；写操作请谨慎。")
