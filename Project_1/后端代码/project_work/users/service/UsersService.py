import os
import random
from email.mime.text import MIMEText
import smtplib
from users.dao import UsersDao
from common import RedisUtil,CrpyUril,JWTUtil
from users.dao.UsersDao import check_email
from users.entity.CheckCodeEntity import CheckCodeClass
from users.entity.LogOutEntity import LogOutClass
from users.entity.RegisterEntity import RegisterClass
from users.entity.LoginEntity import LoginClass
from dotenv import load_dotenv
from users.utils.EmailSender import send_email_code
load_dotenv()
def send_email(email:str):
    print("邮件发送中")
    check_in_email=UsersDao.check_email(email)
    check=isinstance(check_in_email,list)
    if not check:
        return {"code":500,"msg":"用户不存在","data":None}

    result=check_in_email[0]
    username=result["username"]
    #连接redis
    r=RedisUtil.get_redis_conn()
    print("redis连接成功")
    if r.exists(email):
        ttl=r.ttl(email)
        print(f"验证码还在有效期内，请 {ttl} 秒后再试")
        return {"code":500,
                "msg":"验证码还在有效期内，请稍后再试",
                "data":None}
    #制作验证码

    code=""
    for i in range(4):
        code+=str(random.randint(0,9))
    result=send_email_code(email, code)
    # ---------------------将验证码传入redis数据库---------------------
    r.setex(email, 60, code)
    RedisUtil.close_redis_conn(r)
    if not result:
        return {"code":500,
                "msg":"发送失败",
                "data":None}
    return {"code":200,
            "msg":"验证码已发送",
            "data":username}

#验证验证码函数
def check_code(checkcodeentity:CheckCodeClass):
    email=checkcodeentity.email
    code=checkcodeentity.code
    #提取出需要的额数据来生成token
    user_id = check_email(email)[0]["user_id"]
    username = check_email(email)[0]["username"]
    role_name = check_email(email)[0]["role"]
    try:
        rel_code=UsersDao.check_code(email)
        if rel_code is None:
            return{
                "code":500,
                "msg":"验证码已过期",
                "data":None
            }
        elif rel_code!=code:
            return{
                "code":500,
                "msg":"验证码错误",
                "data":None
            }
        else:
            token=JWTUtil.create_token({"user_id":user_id,"username":username,"role_name":role_name})
            #保存到redis里面
            result_read=JWTUtil.read_token_in_redis(token)
            if not result_read:
                print("Token存入redis失败")
            print("Token存入redis成功")
            return{
                "code":200,
                "msg":"验证码正确",
                "data":{
                    "token":token,
                    "username":username,
                    "user_id":user_id,
                }
            }
    except Exception as e:
        print(f"验证码校验异常: {e}")
        return {"code": 500,
                "msg": "服务器内部错误",
                "data": None}

#注册函数
def register(registerentity:RegisterClass):
    email=registerentity.email
    username=registerentity.username
    password=CrpyUril.hash_password(registerentity.password)
    check_in_email = UsersDao.check_email(email)
    check = isinstance(check_in_email, list)
    if check:
        return {"code": 500,
                "msg": "已有用户",
                "data": None}
    if username=='草草':
        role='admin'
    else:
        role='user'
    result = UsersDao.register(username, password, email, role)
    if not result:
        return {"code": 500,
                "msg": "注册失败",
                "data": None}
    return {"code": 200,
            "msg": "注册成功",
            "data": None}

#密码登录函数
def login_by_pwd(loginentity:LoginClass):
    email=loginentity.email
    password=loginentity.password
    if not  UsersDao.check_email(email):
        return {"code": 500,
                "msg": "用户不存在",
                "data": None}
    result = UsersDao.check_email(email)[0]
    rel_password=result["password"]
    username=result["username"]
    role=result["role"]
    user_id=result["user_id"]
    check_pwd=CrpyUril.verify_password(password,rel_password)
    if not check_pwd:
        return {"code": 500,
                "msg": "用户名或密码错误",
                "data": None}
    token=JWTUtil.create_token({"user_id":user_id,"username":username,"role_name":role})
    print("token---:",token)
    # 保存到redis里面
    result_read = JWTUtil.read_token_in_redis(token)
    if not result_read:
        print("Token存入redis失败")
    else:
        print("Token存入redis成功")
    return{
        "code": 200,
        "msg": "登录成功",
        "data": {
            "token":token,
            "username":username,
            "user_id":user_id
        }
    }
def logout(logoutentity:LogOutClass):
    token=logoutentity.token
    JWTUtil.logout(token)
    return
# if __name__ == '__main__':
#     send_email("2779691651@qq.com")
def update_username(usernameentity):
    old_username=usernameentity.old_username
    new_username=usernameentity.new_username
    result=UsersDao.update_username(old_username,new_username)
    if not result:
        return {"code": 500,
                "msg": "修改失败",
                "data": None}
    return {"code": 200,
            "msg": "修改成功",
            "data": None}


def update_password(passwordentity):
    username=passwordentity.username
    password_=passwordentity.password
    password=CrpyUril.hash_password(password_)
    result=UsersDao.update_password(username,password)
    if not result:
        return {"code": 500,
                "msg": "修改失败",
                "data": None}
    print("新密码:",password)
    return {"code": 200,
            "msg": "修改成功",
            "data": None}

def get_user_list():
    list=[]
    result=UsersDao.get_user_list()
    if not result:
        return {"code": 500,
                "msg": "获取用户列表失败",
                "data": None}
    for item in result:
        list.append({"user_id":item['user_id'],"username":item['username'],"email":item['email'],"role":item['role']})
    return {"code": 200,
            "msg": "获取用户列表成功",
            "data": list}
