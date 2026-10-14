"""
三层异常检测包（FR-06 / 契约第 7 节）

分层设计（分层是为了"可解释 + 可降级"）：
    第一层 rule_detector.py    规则阈值检测：硬阈值命中即高危，可解释性最强；
    第二层 trend_detector.py   统计趋势检测：移动平均 + 偏离率 + 连续同向，识别"缓慢恶化"；
    第三层 outlier_detector.py 个性化离群检测：MAD（中位数绝对偏差），识别"个体基线外的突刺"；
    汇总       anomaly_detector.py 纯计算层：合并三层结果并按 metric 去重，**无任何副作用**
              （不写库、不发通知、不写 Redis，AC-10）。

导入约定：可写 `from app.core.detect import detect_anomaly, risk_level_of`。
"""
# 便捷导出（各子模块内部不反向依赖本 __init__，不存在循环导入）
from app.core.detect.anomaly_detector import detect_anomaly, detect_latest, risk_level_of
from app.core.detect.outlier_detector import outlier_detect
from app.core.detect.rule_detector import rule_detect
from app.core.detect.trend_detector import trend_detect

__all__ = [
    "rule_detect",
    "trend_detect",
    "outlier_detect",
    "detect_anomaly",
    "detect_latest",
    "risk_level_of",
]
