"""
MCP Server 内部公共小工具

说明：
    这里只放**无业务语义的小工具函数**，供 mcp_health_server.py 与 tools/*.py 共用，
    避免在各文件里重复写"Redis 字节解码""Bearer Token 提取"之类逻辑。
    不依赖 MCP / FastAPI，纯标准库，可独立单测。
"""
import json
from typing import Any


def decode_redis_message(raw: Any) -> dict:
    """
    把从 Redis List 里取出的元素解码成 dict

    decode_responses=False 时取出的是 bytes，可能是：
        - JSON 字符串（正常情况）
        - 纯文本（异常情况）
    解析失败返回 {"raw": <原文本>}，保证调用方永远拿到 dict。
    """
    if raw is None:
        return {}
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return {"raw": str(raw)}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"raw": raw, "value": data}
    except Exception:
        return {"raw": str(raw)}


def extract_bearer_token(authorization: str | None) -> str:
    """
    从 `Authorization: Bearer xxx` 头里提取 Token

    兼容：大小写不一致的 "bearer"、多余空格、只传 Token 不带头部前缀。
    """
    if not authorization:
        return ""
    text = str(authorization).strip()
    if not text:
        return ""
    parts = text.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return text


if __name__ == "__main__":
    # 自测：解码与 Token 提取的边界情况
    print(decode_redis_message(b'{"to":"138","content":"hi"}'))
    print(decode_redis_message("plain text"))
    print(decode_redis_message(None))
    print(extract_bearer_token("Bearer abc123"))
    print(extract_bearer_token("bearer   abc123"))
    print(extract_bearer_token("abc123"))
    print(extract_bearer_token(""))
