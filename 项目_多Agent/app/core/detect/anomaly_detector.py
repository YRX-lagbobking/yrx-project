"""
三层检测汇总层（纯计算层 / 契约第 7 节 / AC-10）

职责与边界（**硬性要求**）：
    * 只做"取数 + 计算 + 合并"，**绝不**写数据库、发通知、写 Redis、改任何状态；
    * 因此 `detect_anomaly(user_id, mode="manual")` 连续调用 N 次，
      `alert_record` 行数不会变化（AC-10），手动问询与自动检测互不污染（需求文档 10.3）；
    * 副作用（写告警表、双通道通知）只在 `app/core/task/detect_task.py` 里发生。

流程：
    query_latest(user_id)                    → 第一层 rule_detect（最新一条）
    query_range(user_id, metric, trend_days) → 第二层 trend_detect（4 个核心指标）
                                             → 第三层 outlier_detect
    合并去重：同一 metric 同时命中多层时，保留级别更高的一条，其余作为附加说明。

降级链（永不向上抛异常）：
    完整检测（规则 + 趋势 + 离群）
        ↓ 任一环节抛异常
    简化检测 detect_latest（只查最新一条 + 规则层）
        ↓ 仍失败
    返回 [] 并 logger.error
"""
from app.core.config import settings
from app.core.detect.outlier_detector import outlier_detect
from app.core.detect.rule_detector import rule_detect
from app.core.detect.trend_detector import CORE_METRICS, trend_detect
from app.core.logger import logger

# 级别与来源的优先级（用于同 metric 去重时挑"更严重、更可解释"的一条）
LEVEL_RANK = {"高危": 3, "中危": 2, "低危": 1, "正常": 0}
SOURCE_RANK = {"rule": 3, "trend": 2, "outlier": 1}
SOURCE_CN = {"rule": "规则阈值", "trend": "趋势", "outlier": "离群", "manual": "手动"}


def _level_rank(level) -> int:
    """级别 → 排序权重；未知级别按 0 处理，但包含关键字时仍能识别"""
    text = str(level or "").strip()
    if text in LEVEL_RANK:
        return LEVEL_RANK[text]
    for key in ("高危", "中危", "低危", "正常"):
        if key in text:
            return LEVEL_RANK[key]
    return 0


def risk_level_of(anomalies: list[dict]) -> str:
    """
    由异常项列表推导整体风险等级：
        有高危 → "高危"；有中危 → "中危"；有低危 → "低危"；无 → "正常"
    """
    if not anomalies:
        return "正常"
    best = 0
    try:
        for item in anomalies:
            if isinstance(item, dict):
                best = max(best, _level_rank(item.get("level")))
    except Exception as e:
        logger.error(f"风险等级计算异常：{e}")
        return "正常"
    for level, rank in (("高危", 3), ("中危", 2), ("低危", 1)):
        if best == rank:
            return level
    return "正常"


def merge_anomalies(anomalies: list[dict]) -> list[dict]:
    """
    同一 metric 的多层命中合并去重。

    规则：保留级别更高的一条（同级时按 rule > trend > outlier 取更可解释的一条），
          被丢弃的条目把描述追加成"附加说明"，避免信息丢失。
    """
    if not anomalies:
        return []
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in anomalies:
        if not isinstance(item, dict):
            continue
        key = str(item.get("metric") or item.get("type") or "unknown")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    merged: list[dict] = []
    for key in order:
        items = sorted(
            groups[key],
            key=lambda x: (_level_rank(x.get("level")), SOURCE_RANK.get(x.get("source"), 0)),
            reverse=True,
        )
        best = dict(items[0])
        notes = []
        for other in items[1:]:
            description = str(other.get("description") or "").strip()
            if not description:
                continue
            source_cn = SOURCE_CN.get(other.get("source"), str(other.get("source") or "其它"))
            notes.append(f"{source_cn}：{description}")
        if notes:
            best["description"] = f"{best.get('description', '')}；附加说明——" + "；".join(notes)
        merged.append(best)
    return merged


def detect_latest(user_id: str) -> list[dict]:
    """
    简化检测（降级用）：只取最新一条生命体征做规则层判断。
    找不到数据或依赖不可用时返回 []，绝不抛异常。
    """
    try:
        from app.ai.tool.influx_tool import query_latest

        latest = query_latest(user_id) or {}
        if not isinstance(latest, dict) or not latest:
            logger.warning(f"简化检测：用户 {user_id} 没有可用的生命体征数据")
            return []
        return rule_detect(latest)
    except Exception as e:
        logger.error(f"简化检测失败（user_id={user_id}）：{e}")
        return []


def detect_anomaly(user_id: str, mode: str = "auto") -> list[dict]:
    """
    三层检测入口（纯计算）。

    参数：
        user_id: 老人用户编号
        mode:    "auto" 自动检测 / "manual" 手动检测——**只影响日志前缀，不影响检测逻辑**
    返回：
        合并去重后的异常项列表（可能为空）
    """
    prefix = f"[检测|{mode}|{user_id}]"
    # ---------------- 完整检测 ----------------
    try:
        from app.ai.tool.influx_tool import query_latest, query_range

        anomalies: list[dict] = []

        # 第一层：最新一条数据做规则阈值判断
        latest = query_latest(user_id) or {}
        if not isinstance(latest, dict) or not latest:
            logger.warning(f"{prefix} 未取到最新生命体征，规则层跳过")
        else:
            anomalies.extend(rule_detect(latest))

        # 第二层 + 第三层：按指标取近 N 天序列
        series_map: dict[str, list[dict]] = {}
        for metric in CORE_METRICS:
            try:
                series_map[metric] = query_range(user_id, metric, settings.trend_days) or []
            except Exception as e:
                logger.error(f"{prefix} 取 {metric} 序列失败（该指标跳过）：{e}")

        anomalies.extend(trend_detect(series_map, settings.trend_days))
        anomalies.extend(outlier_detect(series_map))

        merged = merge_anomalies(anomalies)
        logger.info(
            f"{prefix} 检测完成：指标 {len(series_map)} 个，明细 {len(anomalies)} 条，"
            f"去重后 {len(merged)} 条，综合等级 {risk_level_of(merged)}"
        )
        return merged
    except Exception as e:
        logger.error(f"{prefix} 完整检测失败（{type(e).__name__}: {e}），降级为简化检测")

    # ---------------- 降级 1：简化检测 ----------------
    try:
        result = detect_latest(user_id)
        logger.warning(f"{prefix} 已降级为简化检测（仅规则层），命中 {len(result)} 条")
        return result
    except Exception as e:
        # ---------------- 降级 2：返回空结果 ----------------
        logger.error(f"{prefix} 简化检测同样失败，返回空结果：{e}")
        return []


if __name__ == "__main__":
    # 自测：不依赖 InfluxDB（不可用时自动走降级链），验证接口稳定、无副作用
    logger.info(f"检测参数：趋势窗口={settings.trend_days} 天，偏离阈值={settings.trend_deviation}")
    result = detect_anomaly("u001", mode="manual")
    logger.info(f"detect_anomaly('u001', 'manual') → {result}")
    logger.info(f"detect_latest('u001') → {detect_latest('u001')}")
    logger.info(
        f"risk_level_of 自测：{risk_level_of([{'level': '高危'}])} / "
        f"{risk_level_of([{'level': '中危'}])} / {risk_level_of([])}（应为 高危/中危/正常）"
    )
