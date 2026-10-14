"""
第二层检测：统计趋势检测（FR-06 / 契约第 7 节 / AC-09）

判定思路（"缓慢恶化"识别）：
    1. 只对 `CORE_METRICS`（心率/收缩压/舒张压/血氧）做趋势判断；
    2. 时序点先按"天"聚合（同一天取均值），再用移动平均算基线；
    3. 最新一天的值偏离基线超过 `settings.trend_deviation`（默认 30%）→ 趋势异常，**中危**；
    4. 最近连续多天同向偏离（每天都高于/低于基线）→ 级别仍为中危，
       但描述升级为"连续 N 天持续升高/降低，需重点关注"（避免与规则层高危混淆）；
    5. 纯计算、无副作用（AC-10）；数据不足 2 天时跳过该指标。
"""
from app.core.config import settings
from app.core.logger import logger

# 参与趋势判断的核心指标（与 anomaly_detector 共用）
CORE_METRICS = ("heart_rate", "blood_pressure_sys", "blood_pressure_dia", "spo2")

# metric → 契约第 7 节允许的 trend_* type
TREND_TYPE_MAP = {
    "heart_rate": "trend_heart_rate",
    "blood_pressure_sys": "trend_blood_pressure",
    "blood_pressure_dia": "trend_blood_pressure_dia",
    "spo2": "trend_spo2",
}

METRIC_META = {
    "heart_rate": ("心率", "次/分"),
    "blood_pressure_sys": ("收缩压", "mmHg"),
    "blood_pressure_dia": ("舒张压", "mmHg"),
    "spo2": ("血氧饱和度", "%"),
}


def _to_float(value):
    """转 float；非法值返回 None（不抛异常）"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_points(series) -> list[tuple[str, float]]:
    """把 [{"time","value"}] 提取成 [(time_text, value)]（跳过非法项，保持原顺序）"""
    points: list[tuple[str, float]] = []
    for item in series or []:
        if isinstance(item, dict):
            time_text = str(item.get("time") or "")
            value = _to_float(item.get("value"))
        else:
            time_text, value = "", _to_float(item)
        if value is not None:
            points.append((time_text, value))
    return points


def _date_key(time_text: str) -> str:
    """时间文本 → 日期键（"2026-09-14T08:00:00" → "2026-09-14"）"""
    text = str(time_text or "")
    return text[:10] if len(text) >= 10 else text


def _to_daily(points: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """按天聚合：同一天取均值，按时间先后排序"""
    buckets: dict[str, list[float]] = {}
    for time_text, value in points:
        key = _date_key(time_text)
        buckets.setdefault(key, []).append(value)
    return [(key, round(sum(values) / len(values), 2)) for key, values in sorted(buckets.items())]


def moving_average(values: list[float], window: int) -> float | None:
    """移动平均：取最后 window 个值的均值；空列表返回 None"""
    if not values:
        return None
    size = max(1, int(window or 1))
    window_values = values[-size:]
    return sum(window_values) / len(window_values)


def _continue_days(daily: list[tuple[str, float]], days: int, deviation: float) -> tuple[int, int]:
    """
    从最新一天往回数，连续"高于基线/低于基线且偏离超过 deviation"的天数

    返回：(连续偏高天数, 连续偏低天数)
    """
    if len(daily) < 2:
        return 0, 0
    values = [value for _, value in daily]
    baseline = moving_average(values[:-1], min(len(values) - 1, max(1, int(days or 7))))
    if not baseline:
        return 0, 0

    high = low = 0
    for value in reversed(values[:-1]):
        rate = (value - baseline) / baseline
        if rate > deviation and high == low:
            high += 1
        elif rate < -deviation and low == high:
            low += 1
        else:
            break
    return high, low


def trend_detect(series_map: dict, days: int = 7) -> list[dict]:
    """
    统计趋势检测（第二层）

    参数：
        series_map: {metric: [{"time","value"}, ...]}（anomaly_detector 传入）
        days:       趋势窗口天数（settings.trend_days，默认 7）
    返回：
        趋势异常项列表（level="中危"、source="trend"）
    """
    anomalies: list[dict] = []
    if not isinstance(series_map, dict):
        return anomalies

    try:
        deviation = float(getattr(settings, "trend_deviation", 0.3) or 0.3)
        window = int(days or getattr(settings, "trend_days", 7) or 7)

        for metric in CORE_METRICS:
            points = _extract_points(series_map.get(metric))
            daily = _to_daily(points)
            if len(daily) < 2:
                continue

            values = [value for _, value in daily]
            baseline = moving_average(values[:-1], min(len(values) - 1, window))
            if not baseline:
                continue
            latest = values[-1]
            rate = (latest - baseline) / baseline
            if abs(rate) <= deviation:
                continue

            direction = "上升" if rate > 0 else "下降"
            cn, unit = METRIC_META.get(metric, (metric, ""))
            high_days, low_days = _continue_days(daily, window, deviation)
            streak = high_days if rate > 0 else low_days
            description = (
                f"{cn}最新值 {latest:g} {unit}，较基线 {round(baseline, 1):g} {unit} "
                f"{direction}约 {abs(rate) * 100:.0f}%，超过 {deviation * 100:.0f}% 偏离阈值"
            )
            if streak >= 1:
                trend_word = "持续升高" if rate > 0 else "持续降低"
                description += f"；最近连续 {streak + 1} 天{trend_word}，需重点关注"
            else:
                description += "；建议结合近期数据持续观察"

            anomalies.append({
                "type": TREND_TYPE_MAP.get(metric, f"trend_{metric}"),
                "level": "中危",
                "value": latest,
                "metric": metric,
                "description": description,
                "source": "trend",
            })
            logger.warning(
                f"趋势层命中：{metric} 最新={latest:g} 基线={round(baseline, 1):g} "
                f"偏离={rate * 100:.0f}%（阈值 {deviation * 100:.0f}%）"
            )
    except Exception as e:  # 趋势层永不向上抛异常
        logger.error(f"趋势检测异常：{e}")
        return []

    return anomalies


if __name__ == "__main__":
    logger.info(f"趋势窗口={getattr(settings, 'trend_days', 7)} 天，偏离阈值={getattr(settings, 'trend_deviation', 0.3)}")
    demo_map = {
        "heart_rate": [
            {"time": "2026-09-08T08:00:00", "value": 72},
            {"time": "2026-09-09T08:00:00", "value": 74},
            {"time": "2026-09-10T08:00:00", "value": 73},
            {"time": "2026-09-11T08:00:00", "value": 75},
            {"time": "2026-09-12T08:00:00", "value": 104},
            {"time": "2026-09-13T08:00:00", "value": 108},
            {"time": "2026-09-14T08:00:00", "value": 112},
        ]
    }
    items = trend_detect(demo_map, 7)
    logger.info(f"持续走高序列命中 {len(items)} 项：{[a['type'] for a in items]}（AC-09 期望 trend_* / 中危）")
    normal_map = {"heart_rate": [
        {"time": f"2026-09-{d:02d}T08:00:00", "value": 74 + d % 3} for d in range(8, 15)
    ]}
    logger.info(f"平稳序列命中 {len(trend_detect(normal_map))} 项（期望 0）")
