"""
三层风险检测自测：规则 / 趋势 / 离群 + 手动检测无副作用（FR-06 / AC-08~AC-10）

推荐运行方式（在项目根目录下执行）：
    python -m app.test.test_detect
    python app/test/test_detect.py

演示内容：
    1. rule_detect(...)        —— 第一层规则阈值：心率 140 → heart_rate_high / 高危（AC-08）；
                                 阈值来自 settings.thresholds，本脚本用假数据直接构造异常项；
    2. trend_detect(...)       —— 第二层统计趋势：连续 3 天同向偏离均值 >30% → 升级中危（AC-09）；
    3. detect_anomaly(user_id, mode) —— 集成三层检测（纯计算层，只读不写）；
    4. risk_level_of(...)      —— 由异常项推导总体风险等级；
    5. **AC-10 无副作用验证**：连续调用 detect_anomaly(mode="manual") 多次，
       记录 AlertDAO.count_today(user_id) 前后是否变化（连不上库就打印跳过）。

依赖：app/core/detect/*、app/dao/alert_dao.py（由 core-dao 同学实现）。
      函数签名可能随实现略有差异，本脚本会逐个函数探测并优雅降级。
"""
import os
import sys

# ---------- 项目根目录加入 sys.path ----------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def print_anomalies(items: list, title: str) -> None:
    """统一打印异常项列表（固定结构见契约第 7 节）"""
    print(f"\n     {title}：共 {len(items)} 项")
    if not items:
        print("       （无异常）")
        return
    for i, a in enumerate(items, 1):
        print(f"       [{i}] type={a.get('type')} level={a.get('level')} "
              f"metric={a.get('metric')} value={a.get('value')} source={a.get('source')}")
        print(f"           description: {a.get('description')}")


def main() -> None:
    print("=" * 72)
    print("三层风险检测自测：规则 / 趋势 / 离群（FR-06 / AC-08~AC-10）")
    print("=" * 72)

    # ---------------- 0. 导入检测模块 ----------------
    rule_detect = trend_detect = detect_anomaly = risk_level_of = None
    try:
        from app.core.detect.rule_detector import rule_detect as _rule
        rule_detect = _rule
    except Exception as e:
        print(f"[提示] rule_detector 不可用：{e}")
    try:
        from app.core.detect.trend_detector import trend_detect as _trend
        trend_detect = _trend
    except Exception as e:
        print(f"[提示] trend_detector 不可用：{e}")
    try:
        from app.core.detect.anomaly_detector import (
            detect_anomaly as _detect,
            risk_level_of as _risk,
        )
        detect_anomaly, risk_level_of = _detect, _risk
    except Exception as e:
        print(f"[提示] anomaly_detector 不可用：{e}")

    if not any([rule_detect, trend_detect, detect_anomaly]):
        print("\n[跳过] 三层检测模块都不可用，请先完成 core-dao 模块（契约第 7 节）。")
        return

    try:
        from app.core.config import settings

        print(f"\n【0】阈值配置 settings.thresholds：")
        for k, v in settings.thresholds.items():
            print(f"       {k:<16}= {v}")
    except Exception:
        settings = None

    # ---------------- 1. 第一层：规则阈值检测（AC-08） ----------------
    print("\n" + "-" * 72)
    print("【1】第一层 规则阈值检测：注入心率 140，应判定 heart_rate_high / 高危（AC-08）")
    print("     口径：硬阈值意味着已超出安全范围，规则层命中一律记 level=\"高危\"、source=\"rule\"")
    if rule_detect is None:
        print("     [跳过] rule_detect 不可用")
    else:
        fake_vitals = {
            "heart_rate": 140,             # > 120 → 高危
            "blood_pressure_sys": 186,     # > 180 → 高危
            "blood_pressure_dia": 118,     # > 110 → 高危
            "spo2": 88,                    # < 90  → 高危
            "temperature": 38.5,           # > 37.3 → 高危
            "glucose": 3.2,                # < 3.9  → 高危
            "sleep_hours": 3,              # 阈值表里有 sleep_low，但 type 白名单里没有睡眠项
        }
        print(f"     构造假体征：{fake_vitals}")
        try:
            items = rule_detect(fake_vitals)
            print_anomalies(items, "规则检测结果")
            types = [x.get("type") for x in items]
            print(f"     AC-08 校验：命中 heart_rate_high = "
                  f"{'heart_rate_high' in types}")
            hr = next((x for x in items if x.get("type") == "heart_rate_high"), None)
            if hr:
                print(f"     该异常项 level = {hr.get('level')}（期望 高危）"
                      f" → {'通过' if hr.get('level') == '高危' else '不通过'}")
            # 正常数据不应产生异常项
            normal = rule_detect({"heart_rate": 75, "blood_pressure_sys": 120,
                                  "blood_pressure_dia": 80, "spo2": 97,
                                  "temperature": 36.5, "glucose": 5.6})
            print(f"     反例校验：正常数据命中 {len(normal)} 项（期望 0）"
                  f" → {'通过' if len(normal) == 0 else '不通过'}")
        except TypeError:
            # 兼容 rule_detect(user_id, vitals) 之类的签名
            try:
                items = rule_detect("u001", fake_vitals)
                print_anomalies(items, "规则检测结果（备用签名）")
            except Exception as e:
                print(f"     [降级] rule_detect 调用失败：{type(e).__name__}: {e}")
        except Exception as e:
            print(f"     [降级] rule_detect 调用失败：{type(e).__name__}: {e}")

    # ---------------- 2. 第二层：统计趋势检测（AC-09） ----------------
    print("\n" + "-" * 72)
    print("【2】第二层 统计趋势检测：连续 3 天心率同向偏离均值 >30% → 趋势异常（中危）（AC-09）")
    if trend_detect is None:
        print("     [跳过] trend_detect 不可用")
    else:
        # trend_detect 的入参是"指标 → 时序点列表"的字典：
        #   {"heart_rate": [{"time": "...", "value": 72}, ...], "spo2": [...]}
        # 构造"前 4 天平稳 + 最近 3 天持续走高"的假序列
        series_map = {
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
        values = [p["value"] for p in series_map["heart_rate"]]
        base = sum(values[:-1]) / max(1, len(values) - 1)
        print(f"     构造假时序（近 7 天心率）：{values}")
        print(f"     历史均值 ≈ {base:.1f}，最新值 {values[-1]}，"
              f"偏离率 ≈ {(values[-1] - base) / base * 100:.0f}%（阈值 "
              f"{settings.trend_deviation if settings else 0.3} → "
              f"{(settings.trend_deviation if settings else 0.3) * 100:.0f}%）")
        try:
            items = trend_detect(series_map, settings.trend_days if settings else 7)
            print_anomalies(items, "趋势检测结果")
            ttypes = [x.get("type") for x in items]
            print(f"     AC-09 校验：命中 trend_* = "
                  f"{[t for t in ttypes if t and str(t).startswith('trend')]}，"
                  f"级别 = {[x.get('level') for x in items]}（期望 中危）")
            # 反例：平稳数据不应命中
            normal_map = {"heart_rate": [
                {"time": f"2026-09-{d:02d}T08:00:00", "value": 74 + d % 3}
                for d in range(8, 15)
            ]}
            print(f"     反例校验：平稳数据命中 {len(trend_detect(normal_map))} 项（期望 0）")
        except TypeError:
            try:
                items = trend_detect("u001", "heart_rate", series_map["heart_rate"])
                print_anomalies(items, "趋势检测结果（备用签名）")
            except Exception as e:
                print(f"     [降级] trend_detect 调用失败：{type(e).__name__}: {e}")
        except Exception as e:
            print(f"     [降级] trend_detect 调用失败：{type(e).__name__}: {e}")

    # ---------------- 3. 集成三层检测 + 风险分级 ----------------
    print("\n" + "-" * 72)
    print("【3】detect_anomaly('u001', mode='manual')：集成三层检测（纯计算层）")
    manual_items = []
    if detect_anomaly is None:
        print("     [跳过] detect_anomaly 不可用")
    else:
        try:
            manual_items = detect_anomaly("u001", mode="manual")
            print_anomalies(manual_items, "detect_anomaly 结果")
            if risk_level_of:
                print(f"     risk_level_of(...) = {risk_level_of(manual_items)}")
        except Exception as e:
            print(f"     [降级] detect_anomaly 调用失败（多半是 InfluxDB 没数据）："
                  f"{type(e).__name__}: {e}")
            print("           预期行为：连不上 InfluxDB 时返回 [] 或走 detect_latest 简化检测，不抛异常。")

    # ---------------- 4. AC-10：手动检测无副作用（关键教学点） ----------------
    print("\n" + "-" * 72)
    print("【4】AC-10 无副作用验证：手动检测不得写 alert_record")
    print("     设计依据：detect_anomaly 是**纯计算层**，只读 InfluxDB 与计算，")
    print("               不写库、不发通知、不改 Redis；mode 只影响日志，不影响检测逻辑。")
    if detect_anomaly is None:
        print("     [跳过] detect_anomaly 不可用，无法演示")
    else:
        try:
            from app.dao.alert_dao import AlertDAO
        except Exception as e:
            print(f"     [跳过] AlertDAO 不可用（无法统计行数，属正常降级）：{e}")
            AlertDAO = None

        if AlertDAO is not None:
            try:
                before = AlertDAO.count_today("u001")
            except Exception as e:
                before = None
                print(f"     [跳过] 连不上 MySQL，无法统计 alert_record 行数：{e}")

            if before is not None:
                print(f"     调用前 alert_record 今日行数 = {before}")
                rounds = 10
                print(f"     连续调用 detect_anomaly('u001', mode='manual') {rounds} 次…")
                for i in range(rounds):
                    try:
                        detect_anomaly("u001", mode="manual")
                    except Exception as e:
                        print(f"       第 {i + 1} 次调用异常（已忽略）：{type(e).__name__}: {e}")
                try:
                    after = AlertDAO.count_today("u001")
                except Exception as e:
                    after = None
                    print(f"     [跳过] 调用后统计失败：{e}")

                if after is not None:
                    print(f"     调用后 alert_record 今日行数 = {after}")
                    if before == after:
                        print(f"     ✔ AC-10 通过：行数未变化（{before} → {after}），手动检测无副作用")
                    else:
                        print(f"     ✘ AC-10 不通过：行数从 {before} 变成 {after}，"
                              f"说明 detect_anomaly 里混入了写库逻辑，请检查！")

    # ---------------- 5. 自动 / 手动差异说明 ----------------
    print("\n" + "-" * 72)
    print("【5】自动检测与手动检测的差异（两者检测逻辑与数据来源完全一致）")
    print("     mode='auto'   → 由 detect_task.auto_detect_all 调用，命中后写 alert_record + 双通道通知")
    print("     mode='manual' → 由 risk 节点 / /alert/detect 调用，只返回结论给 Agent 或前端")
    print("     结论：老人主动问询不会污染自动预警的统计口径。")

    print("\n" + "=" * 72)
    print("三层检测自测结束")
    print("=" * 72)


if __name__ == "__main__":
    main()
