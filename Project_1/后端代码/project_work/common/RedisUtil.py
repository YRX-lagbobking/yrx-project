import redis
import os
from dotenv import load_dotenv
load_dotenv()

def get_redis_conn():
    return redis.Redis(
        host=os.getenv("REDIS_HOST"),
        port=int(os.getenv("REDIS_PORT")),
        db=int(os.getenv("REDIS_DB")),
        decode_responses=True,
        protocol=2  # ✅ 强制使用 RESP2 协议
    )
def get_redis_conn_token():
    return redis.Redis(
        host=os.getenv("REDIS_HOST"),
        port=int(os.getenv("REDIS_PORT")),
        # db=int(os.getenv("REDIS_DB_TOKEN")),
        db=1,
        decode_responses=True,
        protocol=2  # 强制使用 RESP2 协议
    )
def get_redis_black_token():
    return redis.Redis(
        host=os.getenv("REDIS_HOST"),
        port=int(os.getenv("REDIS_PORT")),
        # db=int(os.getenv("REDIS_DB_TOKEN")),
        db=2,
        decode_responses=True,
        protocol=2  # 强制使用 RESP2 协议
    )
def close_redis_conn(conn):
    conn.close()