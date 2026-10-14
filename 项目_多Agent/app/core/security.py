"""
安全模块：JWT 认证 + RBAC 数据权限 + 口令哈希（契约第 4 节，接口锁定）

设计说明：
    1. 优先使用 PyJWT（`import jwt`，函数内延迟导入）；未安装时退化为标准库
       hmac + hashlib + base64 自实现的 HS256 签名，**两种实现的 Token 格式完全一致**
       （header.payload.signature，Base64URL 无填充），因此可以互相解码，接口永远可用；
    2. 口令哈希用 `sha256(salt + 明文)`：教学项目为免去 bcrypt/argon2 的编译依赖而如此设计，
       **生产环境必须改为 bcrypt / argon2（加盐 + 慢哈希 + 逐用户随机盐）**；
    3. `get_current_user` 只负责"解析 Bearer Token 并校验"，白名单路由
       （/health、/user/login、/user/register、/）由路由层决定是否挂载该依赖——
       本模块**不放行**任何路径，避免"依赖里悄悄跳过鉴权"造成越权。
"""
import base64
import hashlib
import hmac
import json
import time

from app.core.config import settings
from app.core.logger import logger

# ---------------- FastAPI / Pydantic（可缺失时降级，保证模块仍可导入） ----------------
try:
    from fastapi import Depends, HTTPException, Request
    from pydantic import BaseModel

    WEB_DEPS_AVAILABLE = True
except ImportError as _e:  # pragma: no cover - 仅在未安装 Web 依赖的教学环境触发
    WEB_DEPS_AVAILABLE = False
    logger.warning(f"fastapi/pydantic 未安装，security 以降级模式提供接口：{_e}")

    class _HTTPExceptionStub(Exception):
        """HTTPException 占位：无 FastAPI 时抛出同语义异常"""

        def __init__(self, status_code: int, detail: str = ""):
            self.status_code = status_code
            self.detail = detail
            super().__init__(f"{status_code}: {detail}")

    class _RequestStub:
        """Request 占位：只需 headers 属性（大小写不敏感由调用方处理）"""

        headers: dict = {}

    class _BaseModelStub:
        """BaseModel 占位：支持关键字构造与 model_dump"""

        def __init__(self, **data):
            self.__dict__.update(data)

        def model_dump(self) -> dict:
            return dict(self.__dict__)

        dict = model_dump

    def _depends_stub(dependency=None, **_kwargs):
        """Depends 占位：直接返回被依赖对象，保持函数签名可导入"""
        return dependency

    HTTPException = _HTTPExceptionStub
    Request = _RequestStub
    BaseModel = _BaseModelStub
    Depends = _depends_stub

# 口令盐值：固定常量，必须与 sql/init.sql 文末"密码哈希说明"逐字一致，
# 否则初始化脚本里的演示账号（u001/f001/admin，明文 123456 / admin123456）无法登录。
# 说明：config.py 未提供 password_salt 配置项，故此处用模块常量；
#       生产环境应改为 bcrypt/argon2 + 每用户随机盐（盐值随哈希一起入库）。
PASSWORD_SALT = "health_butler"

# Token 载荷中的角色白名单
VALID_ROLES = ("elder", "family", "admin")


class TokenData(BaseModel):
    """Token 载荷（契约锁定）：user_id + role"""

    user_id: str
    role: str = "elder"


# =====================================================================================
# 口令哈希
# =====================================================================================
def hash_password(raw: str) -> str:
    """
    口令哈希：sha256(salt + 明文) → 十六进制字符串。

    注意：这是教学级实现（快哈希 + 全项目同一盐）。
    生产请改用 bcrypt / argon2（慢哈希 + 每用户随机盐 + 迭代次数）。
    """
    raw = "" if raw is None else str(raw)
    return hashlib.sha256(f"{PASSWORD_SALT}{raw}".encode("utf-8")).hexdigest()


def verify_password(raw: str, hashed: str) -> bool:
    """校验明文口令与哈希是否匹配（用 compare_digest 防时序攻击）"""
    if not hashed:
        return False
    try:
        return hmac.compare_digest(hash_password(raw), str(hashed))
    except Exception as e:  # 任何异常都视为校验失败，绝不向上抛
        logger.error(f"口令校验异常：{e}")
        return False


# =====================================================================================
# Token 编解码（PyJWT 优先，标准库 HMAC 兜底）
# =====================================================================================
def _b64url_encode(raw: bytes) -> str:
    """Base64URL 编码并去掉填充 '='（JWT 规范）"""
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    """Base64URL 解码并补齐填充"""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(f"{text}{padding}")


def _hmac_sign(signing_input: bytes) -> str:
    """用 jwt_secret_key 做 HMAC-SHA256 签名（标准库兜底实现）"""
    digest = hmac.new(
        settings.jwt_secret_key.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    return _b64url_encode(digest)


def _hmac_encode(payload: dict) -> str:
    """自实现 JWT 编码：header.payload.signature"""
    header = {"alg": "HS256", "typ": "JWT"}
    head_part = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body_part = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    signing_input = f"{head_part}.{body_part}".encode("utf-8")
    return f"{head_part}.{body_part}.{_hmac_sign(signing_input)}"


def _hmac_decode(token: str) -> dict | None:
    """
    自实现 JWT 解码：校验签名 + 过期时间，非法/过期返回 None。
    （PyJWT 已安装时正常情况下不会走到这里，仅作为兜底与互操作保证）
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        head_part, body_part, signature = parts
        signing_input = f"{head_part}.{body_part}".encode("utf-8")
        # 签名比对（compare_digest 防时序攻击）
        if not hmac.compare_digest(_hmac_sign(signing_input), signature):
            return None
        payload = json.loads(_b64url_decode(body_part).decode("utf-8"))
        # 过期校验（exp 为 Unix 秒）
        exp = payload.get("exp")
        if exp is not None and float(exp) < time.time():
            return None
        return payload
    except Exception:
        return None


def create_access_token(user_id: str, role: str) -> str:
    """
    签发访问令牌（HS256，默认 24 小时过期）。

    参数：
        user_id: 用户唯一标识
        role: elder / family / admin
    返回：
        JWT 字符串；任何异常都退化为自实现编码，绝不抛异常
    """
    now = int(time.time())
    payload = {
        "user_id": str(user_id),
        "role": role if role in VALID_ROLES else "elder",
        "iat": now,
        "exp": now + int(settings.jwt_expire_minutes) * 60,
    }
    try:
        import jwt  # 延迟导入 PyJWT

        return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    except ImportError:
        # 未安装 PyJWT：使用标准库 HMAC 自实现（语义完全一致）
        return _hmac_encode(payload)
    except Exception as e:
        logger.error(f"PyJWT 签发失败，改用标准库自实现：{e}")
        return _hmac_encode(payload)


def decode_access_token(token: str) -> TokenData | None:
    """
    解析并校验令牌；非法 / 过期 / 缺少字段一律返回 None。
    """
    if not token or not isinstance(token, str):
        return None
    payload = None
    try:
        import jwt  # 延迟导入 PyJWT

        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except ImportError:
        payload = _hmac_decode(token)
    except Exception:
        # PyJWT 校验失败（过期/签名不符）时再用自实现复核一次：
        # 两者格式一致，签名正确且未过期仍可解出，属兼容性兜底
        payload = _hmac_decode(token)

    if not isinstance(payload, dict):
        return None
    user_id = payload.get("user_id")
    role = payload.get("role")
    if not user_id:
        return None
    return TokenData(user_id=str(user_id), role=role if role in VALID_ROLES else "elder")


# =====================================================================================
# RBAC 数据权限
# =====================================================================================
def can_access_user(
    current: TokenData, target_user_id: str, bound_elder_ids: list[str]
) -> bool:
    """
    判断当前用户是否有权访问目标用户的数据。

    规则（契约第 4 节）：
        admin  → 任意用户
        elder  → 只能访问本人
        family → 只能访问已绑定的老人
    """
    if current is None or not target_user_id:
        return False
    try:
        role = (current.role or "").lower()
        if role == "admin":
            return True
        if role == "elder":
            return str(current.user_id) == str(target_user_id)
        if role == "family":
            return str(target_user_id) in {str(x) for x in (bound_elder_ids or [])}
        # 未知角色一律拒绝（默认拒绝原则）
        logger.warning(f"未知角色 {current.role}，拒绝访问 {target_user_id}")
        return False
    except Exception as e:
        logger.error(f"权限判断异常：{e}")
        return False


# =====================================================================================
# FastAPI 依赖
# =====================================================================================
def extract_bearer_token(request: Request) -> str:
    """从 Authorization: Bearer <token> 中提取 Token；提取不到返回空串"""
    try:
        headers = getattr(request, "headers", None) or {}
        # Starlette 的 headers 是大小写不敏感的 Mapping，普通 dict 需自行兜底
        auth = ""
        try:
            auth = headers.get("authorization") or ""
        except Exception:
            auth = ""
        if not auth:
            for key, value in dict(headers).items():
                if str(key).lower() == "authorization":
                    auth = value or ""
                    break
        if not auth:
            # 兼容场景：浏览器的 EventSource（SSE）无法自定义请求头，
            # 因此允许通过查询参数 ?token=<JWT> 传递（前端 app/html/chat.html 即用此方式）。
            try:
                query_params = getattr(request, "query_params", None)
                query_token = query_params.get("token") if query_params else ""
                if query_token:
                    auth = f"Bearer {query_token}"
            except Exception:
                auth = ""
        if not auth:
            return ""
        parts = str(auth).split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
        # 容错：允许直接传裸 Token
        return str(auth).strip()
    except Exception as e:
        logger.error(f"解析 Authorization 头异常：{e}")
        return ""


async def get_current_user(request: Request) -> TokenData:
    """
    FastAPI 依赖：解析 Authorization: Bearer <token> 并返回 TokenData。

    失败统一抛 HTTPException(401, "未认证或登录已过期")，由路由层决定哪些路径免鉴权。
    """
    token = extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="未认证或登录已过期")
    token_data = decode_access_token(token)
    if token_data is None:
        raise HTTPException(status_code=401, detail="未认证或登录已过期")
    return token_data


async def require_admin(current: TokenData = Depends(get_current_user)) -> TokenData:
    """FastAPI 依赖：要求管理员角色，否则 403"""
    if current is None or (current.role or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current


# =====================================================================================
# 日志脱敏（NFR-03：手机号/邮箱中间打码）——本模块附加的辅助能力
# =====================================================================================
def mask_phone(phone: str) -> str:
    """手机号脱敏：138****8000"""
    text = str(phone or "")
    if len(text) < 7:
        return text
    return f"{text[:3]}****{text[-4:]}"


def mask_email(email: str) -> str:
    """邮箱脱敏：ab***@example.com"""
    text = str(email or "")
    if "@" not in text:
        return text
    name, _, domain = text.partition("@")
    if not name:
        return text
    keep = name[0] if len(name) == 1 else name[:2]
    return f"{keep}***@{domain}"


if __name__ == "__main__":
    # 自测：口令哈希、Token 往返、越权判断（无需任何外部依赖）
    logger.info(f"Web 依赖可用：{WEB_DEPS_AVAILABLE}")
    hashed = hash_password("123456")
    logger.info(f"hash_password('123456') = {hashed}")
    logger.info(f"verify_password 正确口令：{verify_password('123456', hashed)}（应为 True）")
    logger.info(f"verify_password 错误口令：{verify_password('654321', hashed)}（应为 False）")

    token = create_access_token("elder_001", "elder")
    logger.info(f"签发 Token：{token[:48]}...")
    data = decode_access_token(token)
    logger.info(f"解析 Token：{data.model_dump() if data else None}")
    logger.info(f"篡改 Token 解析：{decode_access_token(token + 'x')}（应为 None）")

    elder = TokenData(user_id="elder_001", role="elder")
    family = TokenData(user_id="family_001", role="family")
    admin = TokenData(user_id="admin", role="admin")
    logger.info(f"elder 访问本人：{can_access_user(elder, 'elder_001', [])}（应为 True）")
    logger.info(f"elder 访问他人：{can_access_user(elder, 'elder_002', [])}（应为 False）")
    logger.info(
        f"family 访问绑定老人：{can_access_user(family, 'elder_001', ['elder_001'])}（应为 True）"
    )
    logger.info(f"family 访问未绑定老人：{can_access_user(family, 'elder_009', ['elder_001'])}（应为 False）")
    logger.info(f"admin 访问任意用户：{can_access_user(admin, 'elder_999', [])}（应为 True）")
    logger.info(f"脱敏示例：{mask_phone('13812348000')} / {mask_email('laowang@example.com')}")
