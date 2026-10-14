"""
用户健康档案工具（MCP 工具：get_user_profile）

职责：
    按 `name`（姓名）或 `user_id`（用户编号）查询老人的结构化健康档案，
    供"用药提醒邮件""个性化建议"等场景使用（需求 5.12 执行示例第一步）。

数据来源（契约第 8 节，全部同步 DAO）：
    UserDAO.get_by_id / get_by_name   → 账号信息（姓名、邮箱、电话、角色）
    UserDAO.list_family               → 绑定家属
    UserDAO.get_emergency_contact     → 紧急联系人
    HealthDAO.get_profile             → 健康档案（年龄、性别、慢病史、过敏史）

返回结构：
    {
      "found": bool,
      "user_id": str, "name": str, "age": int|None, "gender": str,
      "disease_history": str, "allergy": str,
      "emergency_contact": str, "emergency_phone": str,
      "email": str, "phone": str, "role": str,
      "family": [...], "msg": str
    }
"""
from typing import Any

from app.core.logger import logger


def get_user_profile(name: str | None = None, user_id: str | None = None) -> dict:
    """
    查询老人健康档案（姓名或用户编号二选一）

    参数：
        name    老人姓名（与 user_id 至少传一个）
        user_id 老人用户编号（优先使用）
    返回：
        档案 dict；查不到返回 {"found": False, "msg": ...}（不抛异常）
    """
    name = (name or "").strip()
    user_id = (user_id or "").strip()
    if not name and not user_id:
        return {"found": False, "msg": "请提供 name 或 user_id 以查询健康档案"}

    # ---------------- 1. 解析用户 ----------------
    user: dict | None = None
    try:
        from app.dao.user_dao import UserDAO                 # 同步 DAO（契约第 8 节）

        if user_id:
            user = UserDAO.get_by_id(user_id)
        if user is None and name:
            user = UserDAO.get_by_name(name)
    except Exception as e:
        logger.error(f"get_user_profile 查询用户失败：{e}")
        return {"found": False, "msg": f"用户数据源不可用：{e}"}

    if not user:
        target = user_id or name
        logger.warning(f"get_user_profile 未找到用户：{target}")
        return {"found": False, "user_id": user_id, "name": name, "msg": f"未找到用户：{target}"}

    # ---------------- 2. 健康档案 ----------------
    resolved_id = str(user.get("user_id") or user_id)
    profile: dict = {}
    try:
        from app.dao.health_dao import HealthDAO

        profile = HealthDAO.get_profile(resolved_id) or {}
    except Exception as e:
        logger.warning(f"get_user_profile 查询健康档案失败（降级为仅账号信息）：{e}")

    # ---------------- 3. 家属与紧急联系人 ----------------
    family: list[dict] = []
    try:
        from app.dao.user_dao import UserDAO

        family = UserDAO.list_family(resolved_id) or []
    except Exception as e:
        logger.warning(f"get_user_profile 查询家属失败：{e}")

    emergency_contact = profile.get("emergency_contact") or ""
    emergency_phone = profile.get("emergency_phone") or ""
    if not emergency_contact or not emergency_phone:
        try:
            from app.dao.user_dao import UserDAO

            contact = UserDAO.get_emergency_contact(resolved_id) or {}
            emergency_contact = emergency_contact or contact.get("name", "")
            emergency_phone = emergency_phone or contact.get("phone", "")
        except Exception as e:
            logger.warning(f"get_user_profile 查询紧急联系人失败：{e}")

    result: dict[str, Any] = {
        "found": True,
        "user_id": resolved_id,
        "name": user.get("user_name") or name,
        "age": _to_int(profile.get("age")),
        "gender": profile.get("gender") or "",
        "disease_history": profile.get("disease_history") or "",
        "allergy": profile.get("allergy") or "",
        "emergency_contact": emergency_contact,
        "emergency_phone": emergency_phone,
        "email": user.get("email") or "",
        "phone": user.get("phone") or "",
        "role": user.get("role") or "elder",
        "family": family,
        "msg": "查询成功",
    }
    logger.info(f"get_user_profile 命中用户 {resolved_id}")
    return result


def _to_int(value: Any) -> int | None:
    """安全转 int（年龄等字段在库里可能是字符串）"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    # 自测：MySQL 不可用时应返回 found=False 且不抛异常
    print(get_user_profile(user_id="elder_001"))
    print(get_user_profile(name="张三"))
    print(get_user_profile())          # 缺参数 → 友好提示
