"""
InfluxDB 时序查询工具入参模型（Pydantic）

对应契约第 9 节 `influx_query_tool`：
    字段固定为 user_id / field / days，用于让大模型只做"只读的历史体征查询"。

为什么只暴露这三个字段：
    写入（write_vitals / write_history）由系统内部的数据管道负责，
    不对大模型开放，避免模型"幻觉出一堆假的体征数据"污染时序库（FR-11 工具边界）。
"""
from pydantic import BaseModel, Field

# 允许查询的生命体征字段（与 InfluxDB measurement 的 field 名一一对应）
ALLOWED_VITAL_FIELDS = [
    "heart_rate",
    "blood_pressure_sys",
    "blood_pressure_dia",
    "spo2",
    "temperature",
    "glucose",
    "sleep_hours",
]


class InfluxSchema(BaseModel):
    """influx_query_tool 的入参"""

    user_id: str = Field(..., description="老人用户编号，例如 elder_001", min_length=1)
    field: str = Field(
        "heart_rate",
        description=f"要查询的生命体征字段，可选：{'/'.join(ALLOWED_VITAL_FIELDS)}",
    )
    days: int = Field(7, description="查询最近多少天的数据（1~90）", ge=1, le=90)


if __name__ == "__main__":
    # 自测：合法入参 + 非法 days 的拦截效果
    print(InfluxSchema(user_id="elder_001", field="spo2", days=3).model_dump())
    try:
        InfluxSchema(user_id="elder_001", field="spo2", days=999)
        print("❌ days 越界未被拦截")
    except Exception as e:
        print(f"✅ days 越界已被拦截：{type(e).__name__}")
