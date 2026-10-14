"""
InfluxDB 时序数据自测（FR-14 / AC-25）

推荐运行方式（在项目根目录下执行）：
    python -m app.test.test_influx
    python app/test/test_influx.py

演示内容：
    1. is_available()             —— 探测 InfluxDB 3 Core 是否可用；
    2. write_vitals(...)          —— 写入一条 vital_signs 数据（tag: user_id / source）；
    3. query_latest(user_id)      —— 取最新一条，验证写入是否生效；
    4. query_range(user_id, field, days=7) —— 查询近 7 天序列，打印点数与最值；
    5. write_history(...)         —— 启动补历史（默认近 7 天、10 分钟粒度）说明。

依赖：influxdb_client_3（可选依赖）。未安装或连不上时打印降级说明并退出。
建库命令（README 也有一份）：
    influxdb3 create database --retention-period 30d --token <TOKEN> agent_db
"""
import os
import sys

# ---------- 项目根目录加入 sys.path ----------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def summarize(points: list, label: str) -> None:
    """打印某个指标的近 7 天统计摘要"""
    if not points:
        print(f"     {label}: 无数据")
        return
    values = [float(p["value"]) for p in points if p.get("value") is not None]
    if not values:
        print(f"     {label}: {len(points)} 个点，但取不到数值")
        return
    print(f"     {label}: {len(values)} 个点 | 均值 {sum(values) / len(values):.1f} "
          f"| 最高 {max(values):.1f} | 最低 {min(values):.1f} "
          f"| 最新时间 {points[-1].get('time', '')}")


def main() -> None:
    print("=" * 72)
    print("InfluxDB 时序数据自测（FR-14 / AC-25）")
    print("=" * 72)

    try:
        from app.ai.tool import influx_tool
    except Exception as e:
        print(f"[跳过] app/ai/tool/influx_tool.py 不可用：{e}")
        print("       请先完成 tools-mcp 模块（契约第 9 节），再运行本脚本。")
        return

    try:
        from app.core.config import settings

        print(f"\n【0】配置：INFLUX_URL={settings.influx_url} "
              f"INFLUX_DATABASE={settings.influx_database} "
              f"measurement={settings.vitals_measurement}")
        print(f"       Token 是否已配置：{'是' if settings.influx_token else '否（未配置会直接降级）'}")
    except Exception:
        settings = None

    # ---------------- 1. 连通性探测 ----------------
    print("\n【1】is_available() 探测 InfluxDB 3 Core")
    try:
        available = bool(influx_tool.is_available())
    except Exception as e:
        available = False
        print(f"     探测时异常（已按不可用处理）：{e}")

    if not available:
        print("[降级] InfluxDB 不可用。影响与对策：")
        print("       · 写：write_vitals 返回 False，检测节点改用 detect_latest 简化检测；")
        print("       · 读：query_latest 返回 {}，data_check 节点标记 data_missing；")
        print("       · 修复：确认 .env 的 INFLUX_URL/INFLUX_TOKEN，并执行建库命令：")
        print("         influxdb3 create database --retention-period 30d --token <TOKEN> agent_db")
        print("       本脚本到此结束（不抛异常，符合 NFR-02 降级不崩栈）。")
        return

    print("     InfluxDB 可用 ✔")

    user_id = "u001"
    field = "heart_rate"

    # ---------------- 2. 写入一条数据 ----------------
    print(f"\n【2】write_vitals('{user_id}', heart_rate=88, blood_pressure_sys=128, ...) 写入一条")
    try:
        ok = influx_tool.write_vitals(
            user_id,
            heart_rate=88,
            blood_pressure_sys=128,
            blood_pressure_dia=82,
            spo2=97,
            temperature=36.5,
            glucose=6.1,
            sleep_hours=6.5,
        )
        print(f"     写入结果：{ok}（{'成功' if ok else '失败'}）")
    except Exception as e:
        print(f"     [降级] 写入异常：{type(e).__name__}: {e}")
        return

    # ---------------- 3. 取最新一条，验证写入生效 ----------------
    print(f"\n【3】query_latest('{user_id}') 取最新一条")
    try:
        latest = influx_tool.query_latest(user_id)
        if not latest:
            print("     没有取到数据（可能是写入延迟或 tag 名称不一致）")
        else:
            for k, v in latest.items():
                print(f"     {k:<22}= {v}")
    except Exception as e:
        print(f"     [降级] 查询异常：{type(e).__name__}: {e}")

    # ---------------- 4. 查询近 7 天序列 ----------------
    print(f"\n【4】query_range('{user_id}', '{field}', days=7) 查近 7 天序列")
    try:
        points = influx_tool.query_range(user_id, field, 7)
        summarize(points, field)
        print(f"     前 3 个点：{points[:3]}")
    except Exception as e:
        print(f"     [降级] 查询异常：{type(e).__name__}: {e}")

    print("\n     顺带看几个关键指标：")
    for f in ("blood_pressure_sys", "blood_pressure_dia", "spo2"):
        try:
            summarize(influx_tool.query_range(user_id, f, 7), f)
        except Exception as e:
            print(f"     {f}: 查询异常 {e}")

    # ---------------- 5. 启动补历史（数据连续性策略） ----------------
    print("\n【5】write_history(user_id, days=7, interval_minutes=10) 启动补历史")
    print("     说明：数据持续产生而服务不常驻 → 服务启动时按 10 分钟粒度回填近 7 天，")
    print("           运行期由 start_simulator([...]) 每 10 分钟补一个点，保证趋势窗口连续（AC-25）。")
    if settings and settings.vitals_backfill_days:
        print(f"     当前配置：VITALS_BACKFILL_DAYS={settings.vitals_backfill_days} "
              f"VITALS_INTERVAL_MINUTES={settings.vitals_interval_minutes}")
    print("     （本脚本不真的回填，避免演示环境写入过多数据；需要时取消下面注释）")
    # count = influx_tool.write_history("u002", 7, 10)
    # print(f"     回填完成，共写入 {count} 条")

    print("\n" + "=" * 72)
    print("InfluxDB 自测结束。要点回顾：")
    print("  · measurement = vital_signs，tag = user_id/source，field = 7 项体征")
    print("  · 保留期只能在建库时设定（InfluxDB 3 Core 不支持按点删除，风险 R-02）")
    print("=" * 72)


if __name__ == "__main__":
    main()
