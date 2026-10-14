"""
第三层检测：个性化离群检测（FR-06 / 契约第 7 节）

判定思路（"个体基线外的突刺"）：
    1. 对每个指标取时序点，样本数 < MIN_POINTS(5) 时跳过（统计量不可靠）；
    2. 用 **MAD（中位数绝对偏差）** 衡量偏离：
           |x - median| / (MAD_SCALE × MAD) > MAD_THRESHOLD(3.0) → 离群；
    3. MAD 为 0（数值几乎不变）时退回标准差判据：
           |x - mean| / std > STD_THRESHOLD(2.0) → 离群；
    4. 每个指标只取"偏离最厉害"的那一个点作为异常项：level="中危"、
       source="outlier"、type=f"outlier_{metric}"（其余离群点数量写进日志）；
    5. 纯计算、无副作用（AC-10）。
"""
from app.core.logger import logger

# 参与离群判断的指标（含全部体征字段）
KNOWN_METRICS = (
    "heart_rate",
    "blood_pressure_sys",
    "blood_pressure_dia",
    "spo2",
    "temperature",
    "glucose",
    "sleep_hours",
)

# MAD 相关常数（正态分布下 1.4826 × MAD ≈ 标准差）
MAD_SCALE = 1.4826
MAD_THRESHOLD = 3.0
STD_THRESHOLD = 2.0
MIN_POINTS = 5

METRIC_META = {
    "heart_rate": ("心率", "次/分"),
    "blood_pressure_sys": ("收缩压", "mmHg"),
    "blood_pressure_dia": ("舒张压", "mmHg"),
    "spo2": ("血氧饱和度", "%"),
    "temperature": ("体温", "℃"),
    "glucose": ("血糖", "mmol/L"),
    "sleep_hours": ("睡眠时长", "小时"),
}


def _to_float(value):
    """转 float；非法值返回 None（不抛异常）"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_values(series) -> list[tuple[str, float]]:
    """把 [{"time","value"}] 提取成 [(time_text, value)]（跳过非法项）"""
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


def _median_and_mad(values: list[float]) -> tuple[float, float]:
    """中位数 + MAD（中位数绝对偏差）"""
    ordered = sorted(values)
    size = len(ordered)
    median = ordered[size // 2] if size % 2 else (ordered[size // 2 - 1] + ordered[size // 2]) / 2
    deviations = sorted(abs(value - median) for value in values)
    mad = deviations[size // 2] if size % 2 else (deviations[size // 2 - 1] + deviations[size // 2]) / 2
    return median, mad


def _mean_and_std(values: list[float]) -> tuple[float, float]:
    """均值 + 标准差（总体标准差）"""
    size = len(values)
    mean = sum(values) / size
    variance = sum((value - mean) ** 2 for value in values) / size
    return mean, variance ** 0.5


def outlier_detect(series_map: dict) -> list[dict]:
    """
    个性化离群检测（第三层）

    参数：
        series_map: {metric: [{"time","value"}, ...]}
    返回：
        离群异常项列表（level="中危"、source="outlier"、type=f"outlier_{metric}"）
    """
    anomalies: list[dict] = []
    if not isinstance(series_map, dict):
        return anomalies

    try:
        for metric, series in series_map.items():
            if metric not in KNOWN_METRICS:
                continue
            points = _extract_values(series)
            if len(points) < MIN_POINTS:
                continue

            values = [value for _, value in points]
            median, mad = _median_and_mad(values)

            # 偏离最厉害的点（按 |x - median| 排序）
            worst_time, worst_value, worst_score = "", None, 0.0
            outlier_count = 0
            for time_text, value in points:
                distance = abs(value - median)
                if mad > 0:
                    score = distance / (MAD_SCALE * mad)
                    hit = score > MAD_THRESHOLD
                else:
                    mean, std = _mean_and_std(values)
                    score = abs(value - mean) / std if std > 0 else 0.0
                    hit = score > STD_THRESHOLD
                if hit:
                    outlier_count += 1
                    if score > worst_score:
                        worst_time, worst_value, worst_score = time_text, value, score

            if not outlier_count or worst_value is None:
                continue

            cn, unit = METRIC_META.get(metric, (metric, ""))
            basis = f"偏离超过 {MAD_THRESHOLD:g} 倍 MAD" if mad > 0 else f"偏离超过 {STD_THRESHOLD:g} 倍标准差"
            description = (
                f"{cn}出现个体离群值 {worst_value:g}（个体基线中位数 {median:g}，{basis}），"
                f"与本人日常水平差异明显"
            )
            anomalies.append({
                "type": f"outlier_{metric}",
                "level": "中危",
                "value": worst_value if not float(worst_value).is_integer() else int(worst_value),
                "metric": metric,
                "description": description,
                "source": "outlier",
            })
            logger.warning(
                f"离群层命中：{metric} 离群值={worst_value:g} 时间={worst_time} 共 {outlier_count} 个离群点"
            )
    except Exception as e:  # 离群层永不向上抛异常
        logger.error(f"离群检测异常：{e}")
        return []

    return anomalies


if __name__ == "__main__":
    logger.info(f"MAD 阈值={MAD_THRESHOLD} 倍（缩放系数 {MAD_SCALE}），最少样本 {MIN_POINTS} 条")
    demo_map = {
        "heart_rate": [{"time": f"2026-09-{d:02d}T08:00:00", "value": 80} for d in range(1, 11)]
        + [{"time": "2026-09-11T08:00:00", "value": 145}],
        "spo2": [{"time": f"2026-09-{d:02d}T08:00:00", "value": 96} for d in range(1, 11)],
    }
    items = outlier_detect(demo_map)
    logger.info(f"离群检测命中 {len(items)} 项：{[a['type'] for a in items]}（期望含 outlier_heart_rate）")
    logger.info(f"正常序列命中 {len(outlier_detect({'heart_rate': [{'time': f'2026-09-{d:02d}T08:00:00', 'value': 80 + d % 3} for d in range(1, 11)]}))} 项（期望 0）")
