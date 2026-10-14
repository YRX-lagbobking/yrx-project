"""
DataCheck 节点：取最新生命体征 + 数据质检（FR-06 / FR-14 的数据连续性）

流程：
    1. `query_latest(user_id)` 取最新一条体征（含 time 字段）；
    2. 检查 7 个体征字段是否齐全：
       heart_rate / blood_pressure_sys / blood_pressure_dia / spo2 / temperature / glucose / sleep_hours；
    3. 缺失字段用 `query_range(user_id, field, settings.trend_days)` 补查（回填历史里找最近一个有效值）；
    4. 仍然缺失 → 记入 data_missing，并在 messages 里说明"数据缺失，建议补充测量"（不阻断流程）；
    5. `data_available = bool(vitals)`：完全没数据时由 Summarize/Chat 提示用户稍后再试或联系家属。

写入状态：vitals、data_available、data_missing、task_results["vitals"]、health_step="data_check"。
"""
from langchain_core.messages import AIMessage

from app.ai.agent.multi_agent.state.health_state import HealthState
from app.ai.tool.influx_tool import query_latest, query_range
from app.core.config import settings
from app.core.logger import logger

# 体征完整性检查字段（与 InfluxDB vital_signs 字段一一对应）
VITAL_FIELDS = (
    "heart_rate",
    "blood_pressure_sys",
    "blood_pressure_dia",
    "spo2",
    "temperature",
    "glucose",
    "sleep_hours",
)

# 字段中文名（给老人看的说明里用中文更友好，也是 messages 说明的基础）
FIELD_CN = {
    "heart_rate": "心率",
    "blood_pressure_sys": "收缩压",
    "blood_pressure_dia": "舒张压",
    "spo2": "血氧",
    "temperature": "体温",
    "glucose": "血糖",
    "sleep_hours": "睡眠时长",
}


def _is_missing(value) -> bool:
    """判断体征值是否"有效"：None / 空串 / 非数字字符串都算缺失"""
    if value is None or value == "":
        return True
    try:
        float(value)
        return False
    except (TypeError, ValueError):
        return True


def _pick_value(points) -> float | None:
    """
    从 query_range 返回的时序点里取最近一个有效值。
    时序点结构约定为 [{"time": str, "value": float}]，这里倒序取第一个能转成 float 的值。
    """
    if not isinstance(points, list):
        return None
    for point in reversed(points):
        if isinstance(point, dict):
            value = point.get("value")
        else:
            value = point
        if not _is_missing(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


async def data_check_node(state: HealthState):
    """数据质检节点：取最新体征并检查完整性"""
    user_id = str(state.get("user_id") or "")
    vitals: dict = {}
    missing: list[str] = []
    try:
        logger.info(f"[DataCheck] 查询用户 {user_id} 的最新体征")
        # 同步工具函数（InfluxDB 查询）：教学/演示规模下直接在 async 节点里调用；
        # 生产环境建议改为异步客户端或连接池，避免阻塞事件循环。
        vitals = query_latest(user_id) or {}
        logger.info(f"[DataCheck] 最新体征：{vitals}")

        # 1. 完整性检查
        missing = [field for field in VITAL_FIELDS if _is_missing(vitals.get(field))]

        # 2. 缺失字段尝试补查（近 trend_days 天里找最近的有效值）
        for field in list(missing):
            try:
                points = query_range(user_id, field, settings.trend_days)
                value = _pick_value(points)
                if value is not None:
                    vitals[field] = value
                    missing.remove(field)
                    logger.info(f"[DataCheck] 字段 {field} 已用近 {settings.trend_days} 天历史补查：{value}")
            except Exception as inner_e:  # 单个字段补查失败不影响其它字段
                logger.error(f"[DataCheck] 字段 {field} 补查失败：{inner_e}")

        # 3. 组织说明消息
        data_available = bool(vitals)
        if not data_available:
            note = "暂时没有查到您的体征数据，建议先用设备测一次（心率、血压、血氧），稍后再问我。"
        elif missing:
            missing_cn = "、".join(FIELD_CN.get(f, f) for f in missing)
            note = f"已取到最新体征，但{missing_cn}数据缺失，建议补充测量。"
        else:
            note = "体征数据完整，可以开始分析。"

        return {
            "vitals": vitals,
            "data_available": data_available,
            "data_missing": missing,
            "task_results": {
                "vitals": {"status": "ok", "vitals": vitals, "missing": missing, "data_available": data_available}
            },
            "health_step": "data_check",
            "messages": [AIMessage(content=note)],
        }
    except Exception as e:
        # 异常：写 error 让 Manager 自动重试（超过 3 次降级 chat），同时保留安全默认值
        logger.error(f"[DataCheck] 数据质检失败：{e}")
        return {
            "vitals": vitals,
            "data_available": False,
            "data_missing": missing or list(VITAL_FIELDS),
            "task_results": {"vitals": {"status": "error", "msg": str(e)}},
            "health_step": "data_check",
            "messages": [AIMessage(content="体征数据获取失败，稍后我再帮您查一次。")],
        }


if __name__ == "__main__":
    # 自测说明：只验证纯函数逻辑（_is_missing / _pick_value），不连 InfluxDB。
    print("缺失判断：", _is_missing(None), _is_missing(""), _is_missing("abc"), _is_missing(72))
    print("取值：", _pick_value([{"time": "t1", "value": None}, {"time": "t2", "value": "88"}]))
    # 真连 InfluxDB 自测（需要配置 INFLUX_TOKEN 且有数据）：
    # import asyncio
    # async def test():
    #     print(await data_check_node({"user_id": "u001", "messages": []}))
    # asyncio.run(test())
