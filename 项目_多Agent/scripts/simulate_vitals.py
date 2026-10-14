"""
体征模拟器：每 N 秒写一条到 InfluxDB，并周期性注入"必中高危规则"的异常值。

用法：
    python scripts/simulate_vitals.py --user u001 --seconds 30 --abnormal-every 3
    python scripts/simulate_vitals.py --once          # 只写一条，快速验证
"""
import argparse, os, random, sys, time
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ); sys.path.insert(0, PROJ)

from app.ai.tool.influx_tool import is_available, write_vitals   # noqa: E402


def normal():
    return dict(
        heart_rate=random.randint(68, 88),
        blood_pressure_sys=random.randint(115, 135),
        blood_pressure_dia=random.randint(70, 85),
        spo2=round(random.uniform(95.0, 98.0), 1),
        temperature=round(random.uniform(36.3, 36.9), 1),
        glucose=round(random.uniform(4.6, 6.0), 1),
        sleep_hours=round(random.uniform(5.5, 7.5), 1),
    )


def abnormal():
    """必中规则层高危（阈值见 .env：HR_HIGH=120 / BP_SYS_HIGH=180 / SPO2_LOW=90）"""
    data, which = normal(), random.choice(("hr", "bp", "spo2"))
    if which == "hr":
        data["heart_rate"] = random.randint(125, 145)
    elif which == "bp":
        data["blood_pressure_sys"] = random.randint(185, 200)
    else:
        data["spo2"] = round(random.uniform(86.0, 89.0), 1)
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="u001")
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--abnormal-every", type=int, default=3, help="每 N 条注入一条异常；0=不注入")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    if not is_available():
        print("❌ InfluxDB 不可用：检查 INFLUX_URL / INFLUX_TOKEN / 数据库 agent_db 是否已建")
        return 1

    print(f"开始模拟 user={a.user} 间隔={a.seconds}s 每{a.abnormal_every}条一次异常（Ctrl+C 停止）")
    i = 0
    while True:
        i += 1
        is_abn = a.abnormal_every > 0 and i % a.abnormal_every == 0
        data = abnormal() if is_abn else normal()
        ok = write_vitals(a.user, **data)
        print(f"[{datetime.now():%H:%M:%S}] #{i} {'🚨异常' if is_abn else '正常'} "
              f"写入{'成功' if ok else '失败'} HR={data['heart_rate']} "
              f"SYS={data['blood_pressure_sys']} SpO2={data['spo2']}")
        if a.once:
            break
        time.sleep(a.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
#cd E:\daimaxiangmu\workspace2\agent_project
# E:\xunienvs\agent_env\python.exe scripts\simulate_vitals.py --user u001 --seconds 30 --abnormal-every 3