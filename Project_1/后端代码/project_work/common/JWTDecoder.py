from typing import Annotated
from fastapi.security import OAuth2PasswordBearer
from common.JWTUtil import verify_token
from fastapi import Depends,Request,status,HTTPException
from common.RedisUtil import get_redis_black_token

# 定义一个OAuth2密码模式
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/checkCode")

# 解码
"""
    Depends：在进入这个方法的时候，要依赖于oauth2_scheme这个方法，等价于拦截处理，获取token
"""
def get_current_user(
        token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    # 解析token，获取用户数据
    payload = verify_token(token)
    id=payload.get("jti")
    r = get_redis_black_token()
    check= r.exists(f"blacklist:{id}")
    if payload is None :
        return {
            "code": 401,
            "msg": "token已过期或错误"
        }
    if check:
        return {
            "code": 401,
            "msg": "token已过期或错误"
        }
    user_id = int(payload.get("user_id"))
    # 判断 --- 假设数据
    if user_id is None:
        return {
            "code": 401,
            "msg": "用户不存在"
        }
    # payload.update({"username": "草草", "role_name": "user"})
    return payload
