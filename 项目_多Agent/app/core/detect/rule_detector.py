"""
第一层检测：规则阈值检测（FR-06 / 契约第 7 节 / AC-08）

设计要点：
    1. 阈值**全部**从 `settings.thresholds` 读取（对应 .env 里的 HR_HIGH / BP_SYS_HIGH 等），
       代码里不出现任何硬编码数字——改阈值不改代码；
    2. 命中规则一律判为"高危"（硬阈值意味着已经超出安全范围）；
    3. `type` 严格使用契约第 7 节的白名单；`sleep_low` 虽在阈值表中，但白名单里没有睡眠项，
       故规则层不产生睡眠异常（避免出现契约外的 type）；
    4. 纯计算、无副作用（AC-10）：不写库、不发通知、不写 Redis。
"""
from app.core.config import settings
from app.core.logger import logger

# 指标元信息：metric → (中文名, 单位)
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
    """转 float；None/空串/非数字返回 None（不抛异常）"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_value(value):
    """数值展示：整数不带小数点（140.0 → '140'），小数保留原样（36.5 → '36.5'）"""
    number = _to_float(value)
    if number is None:
        return str(value)
    return f"{number:g}"


def join_unit(value, unit: str) -> str:
    """数值 + 单位（无单位时只返回数值）"""
    text = format_value(value)
    return f"{text} {unit}".strip() if unit else text


def normalize_value(value):
    """写进异常项的 value 统一成 int/float（保证 JSON 可序列化、前端可比较）"""
    number = _to_float(value)
    if number is None:
        return value
    return int(number) if float(number).is_integer() else number


def build_anomaly(metric: str, type_: str, value, description: str, level: str = "高危") -> dict:
    """按契约第 7 节的固定结构构造异常项"""
    return {
        "type": type_,
        "level": level,
        "value": normalize_value(value),
        "metric": metric,
        "description": description,
        "source": "rule",
    }


def rule_detect(vitals: dict) -> list[dict]:
    """
    规则阈值检测（第一层）

    参数：
        vitals: 最新一条体征 dict（metric → 数值），例如 query_latest() 的返回
    返回：
        异常项列表（全部 level="高危"、source="rule"）
    """
    anomalies: list[dict] = []
    if not isinstance(vitals, dict) or not vitals:
        return anomalies

    try:
        th = settings.thresholds or {}

        def too_high(metric: str, type_: str, value: float, limit) -> None:
            if limit is None or value <= float(limit):
                return
            cn, unit = METRIC_META.get(metric, (metric, ""))
            anomalies.append(
                build_anomaly(
                    metric,
                    type_,
                    value,
                    f"{cn}达到 {join_unit(value, unit)}，超过安全范围（阈值 {join_unit(limit, unit)}）",
                )
            )

        def too_low(metric: str, type_: str, value: float, limit) -> None:
            if limit is None or value >= float(limit):
                return
            cn, unit = METRIC_META.get(metric, (metric, ""))
            anomalies.append(
                build_anomaly(
                    metric,
                    type_,
                    value,
                    f"{cn}仅 {join_unit(value, unit)}，低于安全范围（阈值 {join_unit(limit, unit)}）",
                )
            )

        # 心率
        hr = _to_float(vitals.get("heart_rate"))
        if hr is not None:
            too_high("heart_rate", "heart_rate_high", hr, th.get("hr_high"))
            too_low("heart_rate", "heart_rate_low", hr, th.get("hr_low"))

        # 收缩压 / 舒张压（type 按契约：blood_pressure_high / blood_pressure_low / blood_pressure_dia_high）
        sbp = _to_float(vitals.get("blood_pressure_sys"))
        if sbp is not None:
            too_high("blood_pressure_sys", "blood_pressure_high", sbp, th.get("bp_sys_high"))
            too_low("blood_pressure_sys", "blood_pressure_low", sbp, th.get("bp_sys_low"))
        dbp = _to_float(vitals.get("blood_pressure_dia"))
        if dbp is not None:
            too_high("blood_pressure_dia", "blood_pressure_dia_high", dbp, th.get("bp_dia_high"))

        # 血氧：只有偏低有意义
        spo2 = _to_float(vitals.get("spo2"))
        if spo2 is not None:
            too_low("spo2", "spo2_low", spo2, th.get("spo2_low"))

        # 体温
        temp = _to_float(vitals.get("temperature"))
        if temp is not None:
            too_high("temperature", "temperature_high", temp, th.get("temp_high"))
            too_low("temperature", "temperature_low", temp, th.get("temp_low"))

        # 血糖
        glucose = _to_float(vitals.get("glucose"))
        if glucose is not None:
            too_high("glucose", "glucose_high", glucose, th.get("glucose_high"))
            too_low("glucose", "glucose_low", glucose, th.get("glucose_low"))

        # sleep_hours：阈值表里有 sleep_low，但契约 type 白名单无睡眠项 → 不产生异常项

        if anomalies:
            logger.warning(
                f"规则层命中 {len(anomalies)} 项高危："
                + "；".join(f"{a['type']}={a['value']}" for a in anomalies)
            )
    except Exception as e:  # 规则层永不向上抛异常
        logger.error(f"规则阈值检测异常：{e}")
        return []

    return anomalies


if __name__ == "__main__":
    # 自测：阈值配置 + AC-08 期望（心率 140 → heart_rate_high / 高危）
    logger.info(f"当前阈值：{settings.thresholds}")
    demo = {
        "heart_rate": 140,
        "blood_pressure_sys": 186,
        "blood_pressure_dia": 118,
        "spo2": 88,
        "temperature": 38.5,
        "glucose": 3.2,
        "sleep_hours": 3,
    }
    items = rule_detect(demo)
    logger.info(f"异常数据命中 {len(items)} 项：{[a['type'] for a in items]}")
    normal = rule_detect(
        {"heart_rate": 75, "blood_pressure_sys": 120, "blood_pressure_dia": 80,
         "spo2": 97, "temperature": 36.5, "glucose": 5.6}
    )
    logger.info(f"正常数据命中 {len(normal)} 项（期望 0）")
    high = [a for a in items if a["type"] == "heart_rate_high"]
    if high:
        logger.info(f"心率偏高 type={high[0]['type']} level={high[0]['level']}（AC-08 期望 heart_rate_high / 高危）")
