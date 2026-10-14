"""
用药计划工具（MCP 工具：get_medication_schedule）

职责：
    按 `user_id` 或 `name` 查询老人的用药计划与今日待服清单，
    并附带近 7 天用药依从率（已服 / 总数），供用药提醒与周报使用。

数据来源（契约第 8 节，同步 DAO）：
    UserDAO.get_by_name            → 支持"用姓名查"（需求 5.12 示例里用户只会说"给张三发邮件"）
    MedicationDAO.list_plan        → 用药计划
    MedicationDAO.list_today       → 今日待服
    MedicationDAO.adherence_rate   → 依从率

返回结构（契约固定为 list[dict]，每条含 plan + today 标记）：
    [{"id","drug_name","dosage","frequency","take_time","start_date","end_date",
      "remark","today_status","is_today"}]
"""
from app.core.logger import logger


def _resolve_user_id(user_id: str | None, name: str | None) -> str:
    """把 name 解析成 user_id；解析不到返回原 user_id（可能为空）"""
    user_id = (user_id or "").strip()
    if user_id:
        return user_id
    name = (name or "").strip()
    if not name:
        return ""
    try:
        from app.dao.user_dao import UserDAO

        user = UserDAO.get_by_name(name) or {}
        return str(user.get("user_id") or "")
    except Exception as e:
        logger.warning(f"get_medication_schedule 按姓名解析用户失败：{e}")
        return ""


def get_medication_schedule(user_id: str | None = None, name: str | None = None) -> list[dict]:
    """
    查询用药计划（含今日待服状态）

    参数：
        user_id 老人用户编号（优先）
        name    老人姓名（user_id 为空时用它反查）
    返回：
        用药计划列表；查不到或数据源不可用返回 []（不抛异常）
    """
    resolved = _resolve_user_id(user_id, name)
    if not resolved:
        logger.warning("get_medication_schedule 未能确定用户（user_id/name 均无效）")
        return []

    # ---------------- 1. 用药计划 ----------------
    plans: list[dict] = []
    try:
        from app.dao.medication_dao import MedicationDAO

        plans = MedicationDAO.list_plan(resolved) or []
    except Exception as e:
        logger.error(f"get_medication_schedule 查询用药计划失败：{e}")
        return []

    if not plans:
        logger.info(f"用户 {resolved} 没有用药计划")
        return []

    # ---------------- 2. 今日待服状态 ----------------
    today_map: dict[int, str] = {}
    try:
        from app.dao.medication_dao import MedicationDAO

        for item in MedicationDAO.list_today(resolved) or []:
            medication_id = item.get("medication_id") or item.get("id")
            try:
                today_map[int(medication_id)] = str(item.get("status") or "待服")
            except (TypeError, ValueError):
                continue
    except Exception as e:
        logger.warning(f"get_medication_schedule 查询今日用药失败（降级为计划列表）：{e}")

    # ---------------- 3. 依从率（附带在每条记录上，便于上层直接展示） ----------------
    adherence = None
    try:
        from app.dao.medication_dao import MedicationDAO

        adherence = MedicationDAO.adherence_rate(resolved, 7)
    except Exception as e:
        logger.warning(f"get_medication_schedule 查询依从率失败：{e}")

    result: list[dict] = []
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        try:
            plan_id = int(plan.get("id"))
        except (TypeError, ValueError):
            plan_id = None
        item = {
            "id": plan_id,
            "drug_name": plan.get("drug_name") or "",
            "dosage": plan.get("dosage") or "",
            "frequency": plan.get("frequency") or "",
            "take_time": str(plan.get("take_time") or ""),
            "start_date": str(plan.get("start_date") or ""),
            "end_date": str(plan.get("end_date") or ""),
            "remark": plan.get("remark") or "",
            # 今日有计划但没登记 → 视为"待服"；今日无计划 → "今日无需服用"
            "today_status": today_map.get(plan_id, "待服" if plan_id in today_map else "今日无需服用"),
            "is_today": plan_id in today_map,
            "adherence_rate_7d": adherence,
            "user_id": resolved,
        }
        result.append(item)

    logger.info(f"用户 {resolved} 用药计划 {len(result)} 条")
    return result


if __name__ == "__main__":
    # 自测：MySQL 不可用时应返回 [] 且不抛异常
    print("按 user_id：", get_medication_schedule(user_id="elder_001"))
    print("按姓名：", get_medication_schedule(name="张三"))
    print("缺参数：", get_medication_schedule())
