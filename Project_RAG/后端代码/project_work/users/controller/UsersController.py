from fastapi import APIRouter,Depends

import common.JWTUtil
from chat.entity.CurrentUserEntity import CurrentUserClass
from common.RolePermission import has_roles
from users.entity.CheckCodeEntity import CheckCodeClass
from users.entity.RegisterEntity import RegisterClass
from users.entity.LoginEntity import LoginClass
from users.service import UsersService
#子路由
users_router=APIRouter()

@users_router.get(
    #请求路径
    path="/sendEmail",
    summary="发送邮件",
description ="""
    给用户的邮箱发送验证码，
    请求的路径：http://localhost:8000/users/sendEmail，
    请求的参数：用户的邮箱
    返回的参数：{
        code:为状态码200成功，500失败
        msg:提示的信息
        data:None,数据内容
    }
    """
)
def send_email(email):
    return UsersService.send_email(email)

@users_router.post(
    path="/checkCode",
    summary="检查验证码",
    description ="""
    检查验证码是否正确，
    请求的路径：http://localhost:8000/users/checkCode，
    请求的参数：{
        email:用户的邮箱
        code:验证码
    }
    返回的参数：{
        code:为状态码200成功，500失败
        msg:提示的信息
        data:None,数据内容
    }
    """
)
def check_code(checkcodeentity:CheckCodeClass):
    return UsersService.check_code(checkcodeentity)
@users_router.post(
    path="/register",
    summary="注册",
    description ="""
    注册，
    请求的路径：http://localhost:8000/users/register，
    请求的参数：{
        email:用户的邮箱
        code:验证码
        password:密码
    }
    返回的参数：{
        code:为状态码200成功，500失败
        msg:提示的信息
        data:None,数据内容
    }
    """
)
def register(registerentity:RegisterClass):
    return UsersService.register(registerentity)

@users_router.post(
    path='/loginByPwd',
    summary='密码登录')
def login_by_pwd(loginentity:LoginClass):
    return UsersService.login_by_pwd(loginentity)
from users.entity.LogOutEntity import LogOutClass
@users_router.post(
    path='/logOut',
    summary='退出登录')
def log_out(logoutentity:LogOutClass):
    return UsersService.logout(logoutentity)
from users.entity.UpdateEntity import UsernameClass,PasswordClass
@users_router.post(
    path='/updateUsername',
    summary='修改用户名')
def update_username(usernameentity:UsernameClass):
    return UsersService.update_username(usernameentity)

@users_router.post(
    path='/updatePassword',
    summary='修改密码')
def update_password(passwordentity:PasswordClass):
    return UsersService.update_password(passwordentity)

@users_router.get(
    path='/getUserList',
    summary='获取用户信息'
)
def get_user_list(user:CurrentUserClass = Depends(has_roles(["admin"]))):
    print("get_user_list权限通过",user)
    return UsersService.get_user_list()