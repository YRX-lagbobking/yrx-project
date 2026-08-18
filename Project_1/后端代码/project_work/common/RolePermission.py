# 验证用户是否有访问这个接口的权限,权限管理
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from common.JWTDecoder import get_current_user
# allowed_rols：允许访问的用户角色列表
def has_roles(*allowed_roles: list):
    print(allowed_roles)#这里是一个元组：(['admin'])
    # 定义一个OAuth2密码模式
    oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/checkCode")
    # 判定当前用户是否有访问权限
    def user_permission(token: str = Depends(oauth2_scheme)):

        current_user = get_current_user(token)
        # print(authorization)
        # 这里的authorization的值是 "Bearer token"(这里的token是真实的token哦)
        #如Bearer eyJhbGciOiJIUzI1NiIsIn。。。
        # token = authorization.split(" ")[1]
        # 获取当前用户信息
        # current_user = get_current_user(request)
        print("token",token)
        print("current_user",current_user)
        # 判定
        if current_user['role_name'] in allowed_roles[0]:#注意这里是元组(['admin','user'],)
            return current_user
        else:
            raise HTTPException(status_code=403, detail="无访问权限")

    return user_permission
