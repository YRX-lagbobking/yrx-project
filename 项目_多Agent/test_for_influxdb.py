import os
from influxdb_client_3 import InfluxDBClient3, Point
from dotenv import load_dotenv
load_dotenv()
# 连接配置
client = InfluxDBClient3(
    host=os.getenv("INFLUX_URL"),
    token=os.getenv("INFLUX_TOKEN"), # 或者在代码里直接填你的 Token
    database=os.getenv("INFLUX_DATABASE")
)
# # 写入数据（InfluxDB 3 不需要写 write_api，直接调用 client.write）
# point = (
#     Point("agent_metrics")
#     .tag("host", "windows")
#     .field("cpu_usage", 45.2)
# )
# client.write(record=point)
# print("✅ 写入成功")
# 查询数据（InfluxDB 3 支持直接用 SQL）
table = client.query(query="SELECT * FROM agent_metrics ORDER BY time DESC LIMIT 5")
print(table)
#删除数据
try:
    # 构建 SQL DELETE 语句
    sql = """
          DELETE \
          FROM agent_metrics
          WHERE host = 'windows'
            AND time >= '2026-09-20T00:00:00Z'
            AND time \
              < '2026-09-21T00:00:00Z' \
          """
    client.query(sql)
    print("✅ 删除请求已提交")
except Exception as e:
    print(f"❌ 删除数据时出错: {e}")
# 关闭连接
client.close()