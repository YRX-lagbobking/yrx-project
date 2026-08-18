import json
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
import os
from dotenv import load_dotenv
from common import RedisUtil
import uuid
load_dotenv()
def create_token(data:dict)->str:
    copy_data = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES")))
    copy_data.update({"exp": expire,
                      "iat": datetime.now(timezone.utc),
                      "jti": str(uuid.uuid4()),
                      "username": data.get("username"),
                      "user_id": data.get("user_id"),
                      "role_name": data.get("role_name")})
    token = jwt.encode(
        claims=copy_data,  # 数据
        key=os.getenv("SECRET_KEY"),  # 密钥
        algorithm=os.getenv("ALGORITHM")  # 算法
    )

    return token

def read_token_in_redis(token):
    try:
        r = RedisUtil.get_redis_conn()
        expire_seconds = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES")) * 60
        payload_1 = verify_token(token)
        if payload_1 is None:
            print("verify_token 返回 None，token 无效")
            return False
        user_id = str(payload_1["user_id"])
        read_token = json.dumps(payload_1)
        r.set(f"user_id{user_id}", read_token, ex=expire_seconds)  # 使用 set
        RedisUtil.close_redis_conn(r)
        return True
    except Exception as e:
        print("存储token失败", e)  # 打印异常
        return False


def verify_token(token:str)->dict:
    """
               解码并验证 JWT 令牌
               参数:
                   token: JWT 令牌字符串
               Returns:
                   dict | None: 解码后的 payload，验证失败返回 None
               """
    try:
        payload=jwt.decode(
            token,
            os.getenv("SECRET_KEY"),
            algorithms=[os.getenv("ALGORITHM")]
        )
        return payload
    except JWTError:
        return None

    '''
    {'user_id': 2, 'username': '1', 'role_name': 'user', 'exp': 1786583609, 'iat': 1786583489}
    <class 'dict'>
    '''

def logout(token):
    """将Token加入Redis黑名单，自动在Token原过期时间后清除"""
    try:
        r = RedisUtil.get_redis_conn()
        # 解码时不验证过期时间（因为过期的Token本来就没必要加黑名单，这里为了拿到jti）
        payload = verify_token(token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti and exp:
            # 计算剩余有效时间
            ttl = max(0, int(exp - datetime.now(timezone.utc).timestamp()))
            if ttl > 0:
                # 存入Redis，key包含jti，value随意（设为1），过期时间等于Token剩余时间
                r.setex(f"blacklist:{jti}", ttl, "1")
                print(f"Token {jti} 已拉黑，将于 {ttl} 秒后自动清除")
    except Exception as e:
        print(f"拉黑失败: {e}")
#if __name__ == '__main__':
    #a=verify_token("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoyLCJ1c2VybmFtZSI6IjEiLCJyb2xlX25hbWUiOiJ1c2VyIiwiZXhwIjoxNzg2NTgzODE0LCJpYXQiOjE3ODY1ODM2OTR9.fWBf-hLS4IqlU48Uqq9h2fSGZuK-jVg8YC8yxMk-wZk")
    # print(a)
    # print(type(a))
    # t="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoyLCJ1c2VybmFtZSI6IjEiLCJyb2xlX25hbWUiOiJ1c2VyIiwiZXhwIjoxNzg2NTg2MTc1LCJpYXQiOjE3ODY1ODYwNTV9.MZ_02FnC-nqm27nk0lur1PIcTz5V3fSDdwwxqOFphHI"
    # b=read_token_in_redis(t)
    # print(b)
