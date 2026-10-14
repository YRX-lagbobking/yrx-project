"""
生命体征查询工具（MCP 工具：get_vitals）

职责：
    查询老人最近 days 天的生命体征时序数据，返回"时序点 + 统计摘要 + 异常提示"，
    供风险判断、日报/周报、家属问询等场景使用。

数据来源：
    `app/ai/tool/influx_tool.py` 的 query_range / query_latest
    （该工具内部自带 InfluxDB → 本地 JSON 的硬降级，所以这里天然容错）

返回结构：
    {
      "user_id": str, "field": str|None, "days": int,
      "fields": {...各字段统计...},
      "latest": {...最新一条...},
      "series": {...},                      # 指定 field 时的时序点（最多 500 条）
      "abnormal": [{"field","latest","message"}],   # 与 settings.thresholds 对比的提示
      "msg": str
    }
"""
from app.core.config import settings
from app.core.logger import logger

# 体征字段 → 阈值键（与 settings.thresholds 对应）
THRESHOLD_KEYS = {
    "heart_rate": ("hr_high", "hr_low", "心率"),
    "blood_pressure_sys": ("bp_sys_high", "bp_sys_low", "收缩压"),
    "blood_pressure_dia": ("bp_dia_high", None, "舒张压"),
    "spo2": (None, "spo2_low", "血氧"),
    "temperature": ("temp_high", "temp_low", "体温"),
    "glucose": ("glucose_high", "glucose_low", "血糖"),
    "sleep_hours": (None, "sleep_low", "睡眠时长"),
}

ALL_FIELDS = tuple(THRESHOLD_KEYS.keys())

# 单次返回的最大时序点数（防止 MCP 响应过大）
MAX_POINTS = 500


def _stats(points: list[dict]) -> dict:
    """由时序点计算统计摘要：count/latest/max/min/avg"""
    values = [p["value"] for p in points if isinstance(p.get("value"), (int, float))]
    if not values:
        return {}
    return {
        "count": len(values),
        "latest": values[0],                     # query_range 按时间倒序，第一个即最新
        "max": max(values),
        "min": min(values),
        "avg": round(sum(values) / len(values), 2),
        "latest_time": points[0].get("time", ""),
    }


def _check_thresholds(latest: dict) -> list[dict]:
    """把最新体征与 settings.thresholds 对比，生成异常提示（只提示、不下诊断结论）"""
    abnormal: list[dict] = []
    thresholds = settings.thresholds or {}
    for field, (high_key, low_key, label) in THRESHOLD_KEYS.items():
        value = latest.get(field)
        if not isinstance(value, (int, float)):
            continue
        if high_key and high_key in thresholds and value > thresholds[high_key]:
            abnormal.append({
                "field": field,
                "latest": value,
                "message": f"{label} {value} 超过阈值 {thresholds[high_key]}，建议尽快关注",
            })
        if low_key and low_key in thresholds and value < thresholds[low_key]:
            abnormal.append({
                "field": field,
                "latest": value,
                "message": f"{label} {value} 低于阈值 {thresholds[low_key]}，建议尽快关注",
            })
    return abnormal


def get_vitals(user_id: str, field: str | None = None, days: int = 7) -> dict:
    """
    查询生命体征时序数据

    参数：
        user_id 老人用户编号（必填）
        field   指定体征字段（可选；不传则返回全部字段的统计）
        days    查询最近多少天（默认 7）
    返回：
        时序 + 统计 + 异常提示 dict；数据源不可用时返回 {"user_id":..,"msg":..}（不抛异常）
    """
    user_id = (user_id or "").strip()
    if not user_id:
        return {"user_id": "", "msg": "缺少 user_id 参数"}

    try:
        days = max(1, min(int(days or settings.trend_days or 7), 90))
    except (TypeError, ValueError):
        days = 7

    if field and field not in ALL_FIELDS:
        return {
            "user_id": user_id,
            "msg": f"不支持的体征字段：{field}，可选：{'、'.join(ALL_FIELDS)}",
        }

    result: dict = {
        "user_id": user_id,
        "field": field,
        "days": days,
        "fields": {},
        "latest": {},
        "series": {},
        "abnormal": [],
        "msg": "",
    }

    # ---------------- 1. 最新体征 ----------------
    try:
        from app.ai.tool.influx_tool import query_latest

        result["latest"] = query_latest(user_id) or {}
    except Exception as e:
        logger.error(f"get_vitals 查询最新体征失败：{e}")
        result["msg"] = f"体征数据源不可用：{e}"
        return result

    # ---------------- 2. 时序与统计 ----------------
    targets = [field] if field else list(ALL_FIELDS)
    try:
        from app.ai.tool.influx_tool import query_range

        for item_field in targets:
            points = query_range(user_id, item_field, days) or []
            if field:
                result["series"][item_field] = points[:MAX_POINTS]
            if points:
                result["fields"][item_field] = _stats(points)
    except Exception as e:
        logger.error(f"get_vitals 查询时序失败：{e}")
        result["msg"] = f"体征时序查询失败：{e}"
        return result

    # ---------------- 3. 阈值提示 ----------------
    result["abnormal"] = _check_thresholds(result["latest"])

    if not result["latest"] and not result["fields"]:
        result["msg"] = f"最近 {days} 天没有查询到 {user_id} 的体征数据"
    else:
        result["msg"] = "查询成功"
    logger.info(f"get_vitals 完成：user_id={user_id} field={field} days={days} 异常提示={len(result['abnormal'])}")
    return result


if __name__ == "__main__":
    # 自测：InfluxDB 不可用时降级本地 JSON；无数据时返回空结构，不抛异常
    import json

    demo = get_vitals("elder_demo", days=1)
    print(json.dumps({k: v for k, v in demo.items() if k != "series"}, ensure_ascii=False, indent=2))
    print("指定字段：", get_vitals("elder_demo", field="heart_rate", days=1)["msg"])
    print("非法字段：", get_vitals("elder_demo", field="bad_field")["msg"])
    print("缺 user_id：", get_vitals("")["msg"])
