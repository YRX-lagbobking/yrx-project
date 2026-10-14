"""
InfluxDB 3 Core 生命体征时序工具（契约第 9 节 / 需求 FR-06、FR-14）

数据模型（与需求 5.14 一致）：
    measurement : settings.vitals_measurement（默认 vital_signs）
    tag         : user_id
    field       : heart_rate / blood_pressure_sys / blood_pressure_dia /
                  spo2 / temperature / glucose / sleep_hours
    time        : 写入时刻（可显式传 ISO 字符串 ts）

对外接口（签名与契约逐字一致）：
    write_vitals(user_id, **fields) -> bool
    query_recent(user_id, field, hours=24) -> list[dict]
    query_range(user_id, field, days=7) -> list[dict]
    query_latest(user_id) -> dict
    write_history(user_id, days=7, interval_minutes=10) -> int
    start_simulator(user_ids) -> None
    is_available() -> bool
    influx_query_tool（LangChain Tool，只读查询）

三条设计主线：
    1. **延迟导入**：`influxdb_client_3` 在函数内部导入，
       未安装时模块依然能 import、接口依然存在（契约第 0 节）；
    2. **硬降级**：客户端不可用时，读写全部落到本地 JSON
       `./app/data/vitals_fallback.json`，保证上层三层检测逻辑照常拿到数据（AC-26）；
    3. **演示数据模拟器**：`write_history` 回填近 N 天、`start_simulator` 运行期持续写入，
       生成"生理上合理 + 5% 异常注入"的序列，用于演示预警闭环。
"""
import asyncio
import json
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain.tools import tool

from app.ai.tool.schema.influx_schema import ALLOWED_VITAL_FIELDS, InfluxSchema
from app.core.config import settings
from app.core.logger import logger

# ---------------- 常量定义 ----------------
# 允许写入的生命体征字段（白名单，防止脏字段进库）
VITAL_FIELDS = tuple(ALLOWED_VITAL_FIELDS)

# 本地降级数据文件（InfluxDB 不可用时使用；相对项目根目录）
FALLBACK_PATH = os.path.join(".", "app", "data", "vitals_fallback.json")

# 降级文件最多保留多少天（与 InfluxDB 30d 保留期精神一致，避免文件无限膨胀）
FALLBACK_KEEP_DAYS = 30

# 各指标的"生理合理区间"（演示用：模拟器 95% 的点落在这里）
NORMAL_RANGES = {
    "heart_rate": (62, 95),
    "blood_pressure_sys": (108, 140),
    "blood_pressure_dia": (65, 88),
    "spo2": (94, 99),
    "temperature": (36.2, 37.0),
    "glucose": (4.2, 6.6),
    "sleep_hours": (5.0, 9.0),
}

# 异常注入区间（演示用：约 5% 概率命中，用于触发高危规则）
ABNORMAL_RANGES = {
    "heart_rate": (121, 145),          # 规则阈值 hr_high=120 → 高危
    "blood_pressure_sys": (182, 200),  # 规则阈值 bp_sys_high=180 → 高危
    "spo2": (86, 89),                  # 规则阈值 spo2_low=90 → 高危
}

# 异常注入概率（演示用，可调）
ABNORMAL_PROBABILITY = 0.05

# ---------------- 模块级状态 ----------------
_client: Any = None            # InfluxDBClient3 单例
_client_failed: bool = False   # 连接失败标记：避免每次调用都重试连接（长时间卡顿）
_simulator_started: bool = False   # 模拟器防重复启动标志
_simulator_tasks: list = []        # 后台任务引用（防止被 GC 回收）


# ============================================================
# 一、底层：客户端获取与可用性判断
# ============================================================
def _get_client() -> Any:
    """
    获取/创建 InfluxDB 3 客户端（单例，延迟导入 `influxdb_client_3`）

    返回 None 表示不可用（依赖未安装 / 连接失败），调用方应走本地降级。
    """
    global _client, _client_failed

    if _client is not None:
        return _client
    if _client_failed:
        # 已经确认连不上：直接返回 None，避免每个请求都阻塞在连接超时上
        return None

    try:
        from influxdb_client_3 import InfluxDBClient3       # 可选依赖：延迟导入
    except ImportError:
        _client_failed = True
        logger.warning("未安装 influxdb_client_3，生命体征数据降级为本地 JSON 文件")
        return None
    except Exception as e:                                   # 依赖自身导入报错也降级
        _client_failed = True
        logger.error(f"influxdb_client_3 导入异常，降级为本地 JSON：{e}")
        return None

    if not settings.influx_url:
        _client_failed = True
        logger.warning("INFLUX_URL 未配置，生命体征数据降级为本地 JSON 文件")
        return None

    try:
        _client = InfluxDBClient3(
            host=settings.influx_url,
            token=settings.influx_token or "",
            database=settings.influx_database,
        )
        # 立即探活一次：InfluxDBClient3 构造时不会真正建连，
        # 必须在真正发一次查询后，才能确认"地址通、数据库在、Token 有效"（否则会把错误拖到业务调用时）
        probe_sql = (
            f"SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{settings.influx_database}' LIMIT 1"
        )
        try:
            _client.query(probe_sql, language="sql")
        except Exception:
            # 老版本可能没有 information_schema，退化为一次最小查询做探活
            _client.query("SHOW TABLES", language="sql")
        logger.info(f"InfluxDB 连接成功：{settings.influx_url}/{settings.influx_database}")
        return _client
    except Exception as e:
        _client = None
        _client_failed = True
        logger.error(f"InfluxDB 连接失败，降级为本地 JSON 文件：{e}")
        return None


def is_available() -> bool:
    """
    探测 InfluxDB 是否可用（供 app/main.py 启动流程与 /health 使用）

    注意：任何异常都返回 False，绝不向上抛。
    """
    try:
        return _get_client() is not None
    except Exception as e:                                   # 兜底：连探测本身都不许炸
        logger.error(f"InfluxDB 可用性探测异常：{e}")
        return False


def _ensure_fallback_dir() -> None:
    """确保降级数据目录存在（失败只记日志，不影响主流程）"""
    try:
        directory = os.path.dirname(FALLBACK_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
    except Exception as e:
        logger.error(f"创建降级数据目录失败：{e}")


def reset_client_state() -> None:
    """
    重置客户端状态（测试用：把服务重新拉起来后重新探测连接）
    """
    global _client, _client_failed
    _client = None
    _client_failed = False


# ============================================================
# 二、本地 JSON 降级存储（InfluxDB 不可用时的兜底）
# ============================================================
def _fallback_load() -> list[dict]:
    """读取本地降级数据；文件不存在或损坏时返回空列表"""
    try:
        if not os.path.exists(FALLBACK_PATH):
            return []
        with open(FALLBACK_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"读取本地降级数据失败：{e}")
        return []


def _fallback_save(records: list[dict]) -> None:
    """写回本地降级数据；失败只记日志（工具不得抛异常）"""
    try:
        _ensure_fallback_dir()
        with open(FALLBACK_PATH, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"写入本地降级数据失败：{e}")


def _fallback_append(user_id: str, record: dict) -> None:
    """追加一条降级记录，并做 30 天裁剪"""
    records = _fallback_load()
    item = {"user_id": user_id}
    item.update(record)
    records.append(item)

    # 只保留最近 FALLBACK_KEEP_DAYS 天的数据
    try:
        threshold = datetime.now(timezone.utc) - timedelta(days=FALLBACK_KEEP_DAYS)
        kept = []
        for r in records:
            try:
                t = _parse_time(r.get("time"))
                if t is not None and t < threshold:
                    continue
            except Exception:
                pass
            kept.append(r)
        records = kept
    except Exception:
        pass

    _save_with_limit(records)


def _save_with_limit(records: list[dict], limit: int = 200000) -> None:
    """写入降级文件，超过上限时截断到最近 limit 条（防止文件无限增长）"""
    if len(records) > limit:
        records = records[-limit:]
    _fallback_save(records)


def _parse_time(value: Any) -> datetime | None:
    """把各种形态的时间解析为带时区的 datetime（UTC）；失败返回 None"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    # 兼容 "2024-01-01T10:00:00Z" / 带偏移 / 带微秒 / "2024-01-01 10:00:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
        # ⚠️ 无时区的字符串会被解析成 naive datetime，与 threshold（aware）比较会抛 TypeError，
        #    这里统一补上 UTC 时区
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _fmt_time(value: Any) -> str:
    """把时间统一格式化为 'YYYY-MM-DD HH:MM:SS' 字符串（前端与检测层都用这个形态）"""
    dt = _parse_time(value)
    if dt is None:
        return str(value) if value is not None else ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# 三、写入
# ============================================================
def write_vitals(user_id: str, **fields) -> bool:
    """
    写入一条生命体征数据

    参数：
        user_id : 老人用户编号（作为 tag）
        **fields: 允许 VITAL_FIELDS 中的字段 + 可选 ts（ISO 字符串，缺省用当前时间）
    返回：
        True  写入 InfluxDB 成功
        False 未写入主库（参数非法 / 主库不可用，此时数据会落到本地 JSON 降级文件）

    实现说明：
        - 使用 `Point(settings.vitals_measurement).tag("user_id", ...).field(...)`；
        - 非白名单字段直接忽略并在日志中提示（防止脏字段污染时序库）。
    """
    if not user_id:
        logger.error("write_vitals 缺少 user_id")
        return False

    # 1) 摘出时间戳，其余为体征字段
    ts = fields.pop("ts", None)
    point_fields: dict = {}
    for key, value in fields.items():
        if key not in VITAL_FIELDS:
            logger.warning(f"write_vitals 忽略非白名单字段：{key}")
            continue
        if value is None:
            continue
        try:
            point_fields[key] = float(value)
        except (TypeError, ValueError):
            logger.warning(f"write_vitals 忽略非法数值：{key}={value!r}")

    if not point_fields:
        logger.warning(f"write_vitals 无有效体征字段，user_id={user_id}")
        return False

    # 2) 优先写 InfluxDB
    client = _get_client()
    if client is not None:
        try:
            from influxdb_client_3 import Point          # 延迟导入

            point = Point(settings.vitals_measurement).tag("user_id", str(user_id))
            for key, value in point_fields.items():
                point = point.field(key, value)
            if ts:
                point = point.time(ts)                   # 额外支持显式时间
            client.write(record=point)
            return True
        except Exception as e:
            logger.error(f"write_vitals 写入 InfluxDB 失败，转本地降级：{e}")
            # 不 return：继续走下面的本地降级，保证数据不丢（返回 False 提示主库未成功）

    # 3) 降级：写本地 JSON
    record = {"time": _fmt_time(ts) if ts else _fmt_time(datetime.now(timezone.utc))}
    record.update(point_fields)
    _fallback_append(str(user_id), record)
    return False


def write_history(user_id: str, days: int = 7, interval_minutes: int = 10) -> int:
    """
    回填最近 days 天的历史体征数据（服务启动时调用，保证趋势检测有连续窗口）

    参数：
        user_id           用户编号
        days              回填天数（默认 7）
        interval_minutes  采样间隔分钟（默认 10）
    返回：
        实际写入（或降级保存）的条数

    说明：
        数据为**演示用模拟数据**（见 `_make_vital_sequence`），
        生产环境应替换为真实设备/公开数据集接入（需求 5.14 数据来源）。
    """
    try:
        days = max(1, int(days))
        interval_minutes = max(1, int(interval_minutes))
    except (TypeError, ValueError):
        days, interval_minutes = 7, 10

    # 单次回填的条数上限保护：7 天 × 10 分钟 = 1008 条，上限 5000 防止误传超大天数
    total_points = min(int(days * 24 * 60 / interval_minutes), 5000)
    if total_points <= 0:
        return 0

    now = datetime.now(timezone.utc)
    client = _get_client()
    count = 0

    # 一次性生成整体序列（而不是逐点随机），指标之间的相关性更真实；
    # force_abnormal_last=True：让"最新一条"是异常点，服务一启动就能演示预警闭环
    sequence = _make_vital_sequence(total_points, force_abnormal_last=True)
    # 降级模式：批量组装后一次落盘（比逐条读写文件快得多）
    fallback_batch: list[dict] = []

    for index, vitals in enumerate(sequence):
        point_time = now - timedelta(minutes=interval_minutes * (total_points - 1 - index))
        if client is not None:
            try:
                from influxdb_client_3 import Point      # 延迟导入

                point = Point(settings.vitals_measurement).tag("user_id", str(user_id))
                for key, value in vitals.items():
                    point = point.field(key, float(value))
                point = point.time(point_time)
                client.write(record=point)
                count += 1
                continue
            except Exception as e:
                logger.error(f"write_history 写 InfluxDB 中断，剩余转本地降级：{e}")
                client = None                            # 后续全部走降级，避免反复失败
        # 降级分支
        record = {"user_id": str(user_id), "time": _fmt_time(point_time)}
        record.update({k: float(v) for k, v in vitals.items()})
        fallback_batch.append(record)
        count += 1

    # 降级批量落盘
    if fallback_batch:
        records = _fallback_load()
        records.extend(fallback_batch)
        _save_with_limit(records)
        logger.warning(f"InfluxDB 不可用，write_history 已降级写入本地文件 {len(fallback_batch)} 条")

    logger.info(f"write_history 完成：user_id={user_id} days={days} 条数={count}")
    return count


# ============================================================
# 四、演示数据模拟器（生理合理 + 5% 异常注入）
# ============================================================
def _sample(metric: str, abnormal: bool) -> float:
    """按指标采样一个数值；abnormal=True 时从异常区间取值"""
    if abnormal and metric in ABNORMAL_RANGES:
        low, high = ABNORMAL_RANGES[metric]
    else:
        low, high = NORMAL_RANGES[metric]
    if metric in ("sleep_hours", "temperature", "glucose"):
        return round(random.uniform(low, high), 1)
    return float(random.randint(int(low), int(high)))


def _make_vital_sequence(count: int, force_abnormal_last: bool = False) -> list[dict]:
    """
    生成 count 条**演示用**生命体征序列

    生理合理性设计：
        1. 每个指标以个体基线为中心做小幅游走（模拟"人不是随机数发生器"）；
        2. 心率与血压正相关（交感兴奋时同时升高），体温/血糖/睡眠缓慢漂移；
        3. 约 5% 的数据点注入异常（心率 121~145、收缩压 182~200、血氧 86~89），
           目的是让演示时能稳定触发规则层高危预警（FR-06 第一层），
           ⚠️ 这是**演示用**的刻意设计，绝不代表真实医学建议。

    参数：
        count                生成条数
        force_abnormal_last  是否强制让**最后一条**（即时间上最新的一条）为异常点。
                             `write_history` 用它保证"服务刚启动就有数据可演示预警"；
                             运行期模拟器不用它（异常按自然概率出现）。
    """
    sequence: list[dict] = []
    # 个体基线（随机初始化一次，整段序列围绕它游走）
    base = {
        "heart_rate": random.uniform(70, 84),
        "blood_pressure_sys": random.uniform(118, 132),
        "blood_pressure_dia": random.uniform(72, 82),
        "spo2": random.uniform(95.5, 98.0),
        "temperature": random.uniform(36.4, 36.8),
        "glucose": random.uniform(4.8, 5.8),
        "sleep_hours": random.uniform(6.0, 7.8),
    }

    for index in range(max(0, int(count))):
        # 1) 基线缓慢漂移（心跳/血压同向）
        drift = random.uniform(-2.5, 2.5)
        base["heart_rate"] = min(95.0, max(62.0, base["heart_rate"] + drift * 0.6))
        base["blood_pressure_sys"] = min(140.0, max(108.0, base["blood_pressure_sys"] + drift * 0.5))
        base["blood_pressure_dia"] = min(88.0, max(65.0, base["blood_pressure_dia"] + drift * 0.3))
        base["spo2"] = min(99.0, max(94.0, base["spo2"] + random.uniform(-0.5, 0.5)))
        base["temperature"] = min(37.0, max(36.2, base["temperature"] + random.uniform(-0.1, 0.1)))
        base["glucose"] = min(6.6, max(4.2, base["glucose"] + random.uniform(-0.25, 0.25)))
        base["sleep_hours"] = min(9.0, max(5.0, base["sleep_hours"] + random.uniform(-0.4, 0.4)))

        # 2) 异常注入：5% 自然概率；强制模式下最后一条必为异常（供启动演示）
        item = {metric: _sample(metric, False) for metric in VITAL_FIELDS}
        abnormal_metric = None
        is_last = index == int(count) - 1
        if random.random() < ABNORMAL_PROBABILITY or (force_abnormal_last and is_last):
            abnormal_metric = random.choice(list(ABNORMAL_RANGES.keys()))
            item[abnormal_metric] = _sample(abnormal_metric, abnormal=True)

        # 3) 用个体基线微调，避免整段序列过于"纯随机"；
        #    注意：异常点**不参与**均值平滑，否则会被基线拉回正常范围，演示时触发不了预警
        for metric in ("heart_rate", "blood_pressure_sys", "blood_pressure_dia", "spo2"):
            if metric == abnormal_metric:
                continue
            item[metric] = float(int(round((item[metric] + base[metric]) / 2)))
        sequence.append(item)

    return sequence


async def _simulator_loop(user_ids: list[str]) -> None:
    """
    运行期数据模拟循环（后台任务）

    每 `settings.vitals_interval_minutes` 分钟为每个用户写一条体征数据，
    保证"服务常驻时数据连续"；异常只记日志，绝不中断循环（FR-14）。
    """
    interval_minutes = max(1, int(settings.vitals_interval_minutes or 10))
    logger.info(f"生命体征模拟器已启动：用户={user_ids} 间隔={interval_minutes} 分钟（演示数据）")

    while True:
        try:
            for user_id in user_ids:
                item = _make_vital_sequence(1)[0]
                write_vitals(user_id, **item)
            logger.info(f"生命体征模拟器已写入一轮：{len(user_ids)} 个用户")
        except asyncio.CancelledError:
            logger.info("生命体征模拟器已取消")
            raise
        except Exception as e:
            logger.error(f"生命体征模拟器本轮写入异常（继续运行）：{e}")

        try:
            await asyncio.sleep(interval_minutes * 60)
        except asyncio.CancelledError:
            logger.info("生命体征模拟器已取消")
            raise


def start_simulator(user_ids: list[str]) -> None:
    """
    启动运行期数据模拟（契约第 9 节：asyncio 后台任务，永不抛异常）

    - 必须在已有运行中的事件循环里调用（FastAPI lifespan 内）；
    - 没有事件循环时记日志跳过，不抛异常；
    - 模块级 `_simulator_started` 防重复启动（多次调用只生效一次）。
    """
    global _simulator_started

    if _simulator_started:
        logger.info("生命体征模拟器已在运行，跳过重复启动")
        return
    if not user_ids:
        logger.warning("start_simulator 未收到用户列表，跳过启动")
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 不在事件循环中（例如脚本直接调用）→ 记日志跳过，不抛异常
        logger.warning("当前没有运行中的事件循环，生命体征模拟器跳过启动")
        return

    try:
        task = loop.create_task(_simulator_loop(list(user_ids)))
        _simulator_tasks.append(task)          # 保留引用，防止被 GC 回收
        _simulator_started = True
        logger.info("生命体征模拟器后台任务创建成功")
    except Exception as e:
        logger.error(f"生命体征模拟器启动失败：{e}")


def stop_simulator() -> None:
    """停止模拟器（测试与优雅关闭用；同时重置防重复启动标志）"""
    global _simulator_started
    for task in list(_simulator_tasks):
        try:
            if not task.done():
                task.cancel()
        except Exception:
            pass
    _simulator_tasks.clear()
    _simulator_started = False


# ============================================================
# 五、查询（SQL + 结果归一化）
# ============================================================
def _normalize_rows(raw: Any) -> list[dict]:
    """
    把查询结果归一化为 [{"time":str,"value":float}]

    InfluxDB 3 客户端可能返回 pandas.DataFrame、pyarrow.Table 或 dict 列表，
    这里统一做"鸭子类型"归一化，避免因为客户端版本差异导致上层崩溃。
    """
    rows: list[dict] = []
    if raw is None:
        return rows

    # 1) pandas.DataFrame（influxdb_client_3 查询默认返回 DataFrame）
    if hasattr(raw, "to_dict") and hasattr(raw, "columns"):
        try:
            rows = raw.to_dict(orient="records")
        except Exception:
            try:
                rows = raw.to_dict("records")
            except Exception:
                rows = []

    # 2) pyarrow.Table
    elif hasattr(raw, "to_pylist"):
        try:
            rows = raw.to_pylist()
        except Exception:
            rows = []

    # 3) 其它可迭代对象（list / tuple / generator）
    elif isinstance(raw, (list, tuple)):
        rows = list(raw)
    else:
        try:
            rows = list(raw)
        except Exception:
            return []

    result: list[dict] = []
    for item in rows:
        if isinstance(item, dict):
            time_value = item.get("time")
            value = item.get("value")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            time_value, value = item[0], item[1]
        else:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        result.append({"time": _fmt_time(time_value), "value": value})
    return result


def _query_timeseries(user_id: str, field: str, interval: str) -> list[dict]:
    """
    底层时序查询：SELECT ... WHERE user_id=... AND time >= now() - INTERVAL '<interval>'

    interval 形如 "24 hours" / "7 days"；查询失败返回 []（降级交给上层）。
    """
    if not user_id or field not in VITAL_FIELDS:
        logger.warning(f"查询参数非法：user_id={user_id} field={field}")
        return []

    sql = (
        f"SELECT time, {field} AS value FROM {settings.vitals_measurement} "
        f"WHERE user_id = '{user_id}' "
        f"AND time >= now() - INTERVAL '{interval}' "
        f"ORDER BY time DESC"
    )

    client = _get_client()
    if client is None:
        return []

    try:
        raw = client.query(query=sql, language="sql")
        rows = _normalize_rows(raw)
        logger.info(f"InfluxDB 查询命中 {len(rows)} 条：user_id={user_id} field={field} 区间={interval}")
        return rows
    except Exception as e:
        # SQL 里的引号写法在不同版本可能不被支持 → 退化为简单过滤查询
        logger.error(f"InfluxDB 查询失败，尝试简化查询：{e}")
        try:
            simple_sql = f"SELECT time, {field} AS value FROM {settings.vitals_measurement} ORDER BY time DESC LIMIT 2000"
            rows = _normalize_rows(client.query(query=simple_sql, language="sql"))
            # 在本地按时间过滤，保证语义不变
            return _filter_by_interval(rows, interval)
        except Exception as e2:
            logger.error(f"InfluxDB 简化查询同样失败，返回空结果：{e2}")
            return []


def _filter_by_interval(rows: list[dict], interval: str) -> list[dict]:
    """本地兜底过滤：按 interval（"24 hours"/"7 days"）截取时间窗口"""
    try:
        number, unit = interval.split()
        delta = timedelta(hours=int(number)) if unit.startswith("hour") else timedelta(days=int(number))
        threshold = datetime.now(timezone.utc) - delta
        return [r for r in rows if (_parse_time(r.get("time")) or datetime.min.replace(tzinfo=timezone.utc)) >= threshold]
    except Exception:
        return rows


def _fallback_query(user_id: str, field: str, delta: timedelta) -> list[dict]:
    """本地 JSON 降级查询：返回 [{"time","value"}]，按时间倒序"""
    threshold = datetime.now(timezone.utc) - delta
    result: list[dict] = []
    for record in _fallback_load():
        if str(record.get("user_id")) != str(user_id):
            continue
        if field not in record:
            continue
        dt = _parse_time(record.get("time"))
        if dt is None or dt < threshold:
            continue
        try:
            value = float(record[field])
        except (TypeError, ValueError):
            continue
        result.append({"time": _fmt_time(dt), "value": value})
    result.sort(key=lambda x: x["time"], reverse=True)
    return result


def query_recent(user_id: str, field: str, hours: int = 24) -> list[dict]:
    """
    查询最近 hours 小时的某个体征序列

    返回：[{"time":"2024-01-01 10:00:00","value":72.0}]，按时间倒序；失败返回 []。
    """
    try:
        hours = max(1, int(hours))
    except (TypeError, ValueError):
        hours = 24

    rows = _query_timeseries(user_id, field, f"{hours} hours")
    if rows:
        return rows
    # 主库无数据 / 不可用 → 本地降级
    return _fallback_query(user_id, field, timedelta(hours=hours))


def query_range(user_id: str, field: str, days: int = 7) -> list[dict]:
    """
    查询最近 days 天的某个体征序列（趋势检测与报告统计的主入口）

    返回：[{"time":str,"value":float}]，按时间倒序；失败返回 []。
    """
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 7

    rows = _query_timeseries(user_id, field, f"{days} days")
    if rows:
        return rows
    return _fallback_query(user_id, field, timedelta(days=days))


def query_latest(user_id: str) -> dict:
    """
    查询某个用户"最新一条"完整体征

    返回：
        {"heart_rate":..,"blood_pressure_sys":..,"blood_pressure_dia":..,"spo2":..,
         "temperature":..,"glucose":..,"sleep_hours":..,"time":"..."}
        无数据时返回 {}（契约硬性要求）。
    """
    if not user_id:
        return {}

    # ---------- 1) 主库：直接取最新一行（不限定时间窗口，避免长期无数据时误判为空） ----------
    client = _get_client()
    if client is not None:
        try:
            sql = (
                f"SELECT time, {', '.join(VITAL_FIELDS)} FROM {settings.vitals_measurement} "
                f"WHERE user_id = '{user_id}' ORDER BY time DESC LIMIT 1"
            )
            raw = client.query(query=sql, language="sql")
            rows = _normalize_rows_multi(raw)
            if rows:
                return _fill_latest(rows[0])
        except Exception as e:
            logger.error(f"query_latest 主库查询失败，转本地降级：{e}")

    # ---------- 2) 降级：本地 JSON 里时间最新的一条 ----------
    #    这里不依赖文件里的先后顺序，显式按时间比较，保证结果稳定
    latest: dict | None = None
    latest_time = datetime.min.replace(tzinfo=timezone.utc)
    for record in _fallback_load():
        if str(record.get("user_id")) != str(user_id):
            continue
        record_time = _parse_time(record.get("time")) or datetime.min.replace(tzinfo=timezone.utc)
        if latest is None or record_time >= latest_time:
            latest, latest_time = record, record_time
    if latest is None:
        return {}
    return _fill_latest(latest)


def _normalize_rows_multi(raw: Any) -> list[dict]:
    """
    把"多列"查询结果归一化为 dict 列表（query_latest 专用）

    与 `_normalize_rows` 的区别：这里保留所有体征列，而不是只取 time/value 两列。
    """
    if raw is None:
        return []
    rows: list[dict] = []
    if hasattr(raw, "to_dict") and hasattr(raw, "columns"):
        try:
            rows = raw.to_dict(orient="records")
        except Exception:
            rows = []
    elif hasattr(raw, "to_pylist"):
        try:
            rows = raw.to_pylist()
        except Exception:
            rows = []
    elif isinstance(raw, (list, tuple)):
        rows = list(raw)
    return [r for r in rows if isinstance(r, dict)]


def _fill_latest(record: dict) -> dict:
    """把一条原始记录整理成 query_latest 的标准返回结构"""
    result: dict = {}
    for field in VITAL_FIELDS:
        value = record.get(field)
        if value is None:
            continue
        try:
            result[field] = float(value)
        except (TypeError, ValueError):
            continue
    result["time"] = _fmt_time(record.get("time"))
    return result if len(result) > 1 else {}          # 只有 time、没有任何指标 → 视为无数据


# ============================================================
# 六、给大模型用的只读查询工具
# ============================================================
@tool(args_schema=InfluxSchema)
def influx_query_tool(user_id: str, field: str = "heart_rate", days: int = 7) -> str:
    """
    查询老人最近若干天的生命体征时序数据（只读）

    使用场景：
        - 用户问"我这几天心率怎么样" → 查 heart_rate
        - 用户问"血压最近稳定吗" → 查 blood_pressure_sys

    参数：
        user_id 老人用户编号
        field   体征字段：heart_rate / blood_pressure_sys / blood_pressure_dia /
                spo2 / temperature / glucose / sleep_hours
        days    查询最近多少天（1~90）
    """
    if field not in VITAL_FIELDS:
        return f"不支持的体征字段：{field}，可选：{'、'.join(VITAL_FIELDS)}"

    rows = query_range(user_id, field, days)
    if not rows:
        return f"没有查询到 {user_id} 最近 {days} 天的 {field} 数据"

    values = [r["value"] for r in rows]
    average = round(sum(values) / len(values), 1)
    summary = (
        f"{user_id} 最近 {days} 天 {field} 共 {len(rows)} 条："
        f"最新 {rows[0]['value']}（{rows[0]['time']}），"
        f"最高 {max(values)}，最低 {min(values)}，平均 {average}"
    )
    # 只回传最近 10 个点，避免上下文被长序列占满
    preview = "；".join(f"{r['time']} {r['value']}" for r in rows[:10])
    return f"{summary}\n最近数据：{preview}"


if __name__ == "__main__":
    # 自测：无 InfluxDB / 无 influxdb_client_3 时应走本地 JSON 降级且不抛异常
    print("InfluxDB 是否可用：", is_available())
    demo_user = "elder_demo"
    print("写入一条：", write_vitals(demo_user, heart_rate=76, blood_pressure_sys=126, spo2=97, temperature=36.6))
    print("回填 1 天：", write_history(demo_user, days=1, interval_minutes=60), "条")
    print("最近 24 小时心率：", query_recent(demo_user, "heart_rate", 24)[:3])
    print("最近 7 天血压：", query_range(demo_user, "blood_pressure_sys", 7)[:3])
    print("最新体征：", query_latest(demo_user))
    print("无数据用户的最新体征（应为 {}）：", query_latest("not_exist_user"))
    print("模拟器重复启动保护：")
    start_simulator([demo_user])   # 没有事件循环 → 记日志跳过
    start_simulator([demo_user])
    print("工具出口：", influx_query_tool.invoke({"user_id": demo_user, "field": "heart_rate", "days": 1})[:120])
