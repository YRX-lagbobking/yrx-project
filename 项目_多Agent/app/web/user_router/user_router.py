"""
用户路由（注册 / 登录 / 当前用户 / 家属绑定）

职责：
    1. POST /user/register   ：注册（角色仅允许 elder / family / admin，密码哈希后入库）；
    2. POST /user/login      ：登录并签发 JWT；
    3. GET  /user/me         ：当前用户信息 + 用户画像（L4 记忆）；
    4. POST /user/bind_family：家属绑定老人。

设计说明：
    1. 密码只以 hash_password 结果入库，接口永不回显密码（NFR-03）；
    2. DAO 为同步函数（pymysql），在 async 路由里统一用 asyncio.to_thread 包装
       （本项目为教学/演示规模，生产应改异步连接池）；
    3. 画像、绑定关系等"增强信息"读取失败只降级为空，不影响主流程可用性。
"""
import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from app.core.logger import logger
from app.core.security import TokenData, create_access_token, get_current_user, hash_password

user_router = APIRouter()

# 允许注册的角色（RBAC 三角色）
ALLOWED_ROLES = ("elder", "family", "admin")
# 家属绑定接口允许调用的角色
BIND_ALLOWED_ROLES = ("family", "admin")


class RegisterSchema(BaseModel):
    """注册入参：角色只允许 elder / family / admin"""

    user_name: str = Field(..., description="登录名（唯一）")
    password: str = Field(..., min_length=1, description="明文密码，服务端哈希后入库")
    email: str = Field(default="", description="邮箱")
    phone: str = Field(default="", description="手机号")
    role: str = Field(default="elder", description="角色：elder / family / admin")
    name: str | None = Field(default=None, description="姓名（可选，写入用户画像）")
    age: int | None = Field(default=None, description="年龄（可选，写入健康档案）")
    gender: str | None = Field(default=None, description="性别（可选，写入健康档案）")


class LoginSchema(BaseModel):
    """登录入参"""

    user_name: str = Field(..., description="登录名")
    password: str = Field(..., description="明文密码")


class BindSchema(BaseModel):
    """家属绑定老人入参"""

    elder_id: str = Field(..., description="被绑定的老人 user_id")


@user_router.post("/user/register")
async def user_register(payload: RegisterSchema):
    """
    注册

    流程：角色校验 → 查重 → 密码哈希入库 → （可选）写入健康档案
    """
    logger.info(f"注册请求：user_name={payload.user_name}, role={payload.role}")
    # 1. 角色白名单校验（防止前端伪造角色）
    role = (payload.role or "elder").strip().lower()
    if role not in ALLOWED_ROLES:
        logger.warning(f"非法角色注册被拒绝：{payload.role}")
        return JSONResponse(
            status_code=400,
            content={"code": 400, "msg": "角色只能是 elder / family / admin", "data": None},
        )
    if not payload.user_name.strip() or not payload.password:
        return JSONResponse(status_code=400, content={"code": 400, "msg": "用户名与密码不能为空", "data": None})

    try:
        from app.dao.user_dao import UserDAO

        # 2. 查重（DAO 同步 → 线程池）
        exists = await asyncio.to_thread(UserDAO.get_by_name, payload.user_name)
        if exists:
            logger.warning(f"用户名已存在：{payload.user_name}")
            return JSONResponse(status_code=400, content={"code": 400, "msg": "用户名已存在", "data": None})

        # 3. 密码哈希后入库（绝不存明文）
        hashed = hash_password(payload.password)
        user_id = await asyncio.to_thread(
            UserDAO.create,
            payload.user_name,
            hashed,
            payload.email or "",
            payload.phone or "",
            role,
        )
        # create 返回 lastrowid（整型）时用它拼 user_id，返回字符串时直接用
        if isinstance(user_id, int):
            real_user_id = str(user_id)
        else:
            real_user_id = str(user_id or payload.user_name)

        # 4. 顺带写入健康档案（可选字段，失败忽略——档案可后续补录）
        if payload.age is not None or payload.gender:
            try:
                from app.dao.health_dao import HealthDAO

                await asyncio.to_thread(HealthDAO.save_profile, real_user_id, age=payload.age, gender=payload.gender)
            except Exception as e:
                logger.warning(f"健康档案写入失败（忽略）：{e}")

        logger.info(f"注册成功：user_name={payload.user_name}, user_id={real_user_id}, role={role}")
        return {
            "code": 200,
            "msg": "注册成功",
            "data": {"user_id": real_user_id, "user_name": payload.user_name, "role": role},
        }
    except Exception as e:
        logger.error(f"注册异常：{e}")
        return JSONResponse(status_code=500, content={"code": 500, "msg": "注册失败", "data": None})


@user_router.post("/user/login")
async def user_login(payload: LoginSchema):
    """
    登录

    验证通过后签发 JWT（含 user_id / role / exp），返回 token 与基本用户信息。
    """
    logger.info(f"登录请求：user_name={payload.user_name}")
    try:
        from app.dao.user_dao import UserDAO

        # verify_login 内部完成"取用户 + 校验密码哈希"，失败返回 None
        user = await asyncio.to_thread(UserDAO.verify_login, payload.user_name, payload.password)
        if not user:
            logger.warning(f"登录失败：user_name={payload.user_name}")
            return JSONResponse(status_code=401, content={"code": 401, "msg": "用户名或密码错误", "data": None})

        user_id = str(user.get("user_id") or "")
        role = str(user.get("role") or "elder")
        token = create_access_token(user_id, role)
        logger.info(f"登录成功：user_id={user_id}, role={role}")
        return {
            "code": 200,
            "msg": "登录成功",
            "data": {
                "token": token,
                "user_id": user_id,
                "role": role,
                "user_name": user.get("user_name") or payload.user_name,
            },
        }
    except Exception as e:
        logger.error(f"登录异常：{e}")
        return JSONResponse(status_code=500, content={"code": 500, "msg": "登录失败", "data": None})


@user_router.get("/user/me")
async def user_me(current: TokenData = Depends(get_current_user)):
    """
    当前用户信息 + 用户画像（L4 记忆）

    画像（ProfileMemory）读取失败只忽略，不阻断接口。
    """
    logger.info(f"查询当前用户：user_id={current.user_id}, role={current.role}")
    info = {"user_id": current.user_id, "role": current.role}
    profile = None
    bound_elders: list[str] = []
    family: list[dict] = []

    try:
        from app.dao.user_dao import UserDAO

        row = await asyncio.to_thread(UserDAO.get_by_id, current.user_id)
        if row:
            # 去掉敏感字段，接口永不回显密码
            info.update({k: v for k, v in row.items() if k not in ("password", "hashed_password")})
        # 画像（诊断画像）
        try:
            from app.dao.health_dao import HealthDAO

            profile = await asyncio.to_thread(HealthDAO.get_profile, current.user_id)
        except Exception as e:
            logger.warning(f"健康档案读取失败（忽略）：{e}")
        # 关系：家属看绑定的老人，老人看自己的家属
        try:
            if current.role == "family":
                bound_elders = [str(x) for x in (await asyncio.to_thread(UserDAO.list_bound_elders, current.user_id) or [])]
            elif current.role == "elder":
                family = await asyncio.to_thread(UserDAO.list_family, current.user_id) or []
        except Exception as e:
            logger.warning(f"绑定关系读取失败（忽略）：{e}")
    except Exception as e:
        logger.error(f"用户信息查询失败（降级为 Token 信息）：{e}")

    # 用户画像（L4 记忆）：Redis 不可用时降级为空
    memory_profile = ""
    try:
        from app.ai.agent.memory.save.profile_memory import ProfileMemory

        memory_profile = await ProfileMemory(current.user_id).query() or ""
    except Exception as e:
        logger.warning(f"用户画像读取失败（忽略）：{e}")

    return {
        "code": 200,
        "msg": "查询成功",
        "data": {
            "user_id": current.user_id,
            "role": current.role,
            "user_name": info.get("user_name"),
            "email": info.get("email"),
            "phone": info.get("phone"),
            "profile": profile,
            "memory_profile": memory_profile,
            "bound_elders": bound_elders,
            "family": family,
        },
    }


@user_router.post("/user/bind_family")
async def bind_family(payload: BindSchema, current: TokenData = Depends(get_current_user)):
    """家属绑定老人（仅 family / admin 可调；绑定关系即家属的数据权限来源）"""
    logger.info(f"绑定请求：elder_id={payload.elder_id}, current={current.user_id}, role={current.role}")
    # 仅家属 / 管理员可调用
    if current.role not in BIND_ALLOWED_ROLES:
        logger.warning(f"角色 {current.role} 无权绑定家属关系")
        return JSONResponse(status_code=403, content={"code": 403, "msg": "仅家属或管理员可绑定", "data": None})

    elder_id = str(payload.elder_id or "").strip()
    if not elder_id:
        return JSONResponse(status_code=400, content={"code": 400, "msg": "elder_id 不能为空", "data": None})

    try:
        from app.dao.user_dao import UserDAO

        # 被绑定对象必须是已存在的老人（防止绑定任意 ID 造成脏数据）
        elder = await asyncio.to_thread(UserDAO.get_by_id, elder_id)
        if not elder:
            return JSONResponse(status_code=404, content={"code": 404, "msg": "老人用户不存在", "data": None})
        if str(elder.get("role") or "") != "elder":
            return JSONResponse(status_code=400, content={"code": 400, "msg": "被绑定用户不是老人角色", "data": None})

        ok = await asyncio.to_thread(UserDAO.bind_family, elder_id, current.user_id)
        logger.info(f"绑定结果：elder_id={elder_id}, family_id={current.user_id}, ok={ok}")
        if not ok:
            return JSONResponse(status_code=500, content={"code": 500, "msg": "绑定失败", "data": None})
        return {
            "code": 200,
            "msg": "绑定成功",
            "data": {"elder_id": elder_id, "family_id": current.user_id},
        }
    except Exception as e:
        logger.error(f"绑定异常：{e}")
        return JSONResponse(status_code=500, content={"code": 500, "msg": "绑定失败", "data": None})


# ---------------- 调用示例 ----------------
# 1) 注册
#    curl -X POST http://localhost:8000/user/register -H "Content-Type: application/json" \
#         -d '{"user_name":"zhangsan","password":"123456","email":"a@b.com","phone":"13800000000","role":"elder","age":72,"gender":"男"}'
# 2) 登录（拿到 token）
#    curl -X POST http://localhost:8000/user/login -H "Content-Type: application/json" \
#         -d '{"user_name":"zhangsan","password":"123456"}'
# 3) 当前用户 + 画像
#    curl http://localhost:8000/user/me -H "Authorization: Bearer <token>"
# 4) 家属绑定老人
#    curl -X POST http://localhost:8000/user/bind_family -H "Authorization: Bearer <token>" \
#         -H "Content-Type: application/json" -d '{"elder_id":"1"}'
