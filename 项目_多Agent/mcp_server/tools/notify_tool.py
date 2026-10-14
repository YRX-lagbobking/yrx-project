"""
通知工具（MCP 工具：send_sms / send_email）

⚠️ 关于短信通道的重要说明（教学/演示项目）：
    本项目**没有接入真实短信网关**（也未购买任何运营商通道）。
    `send_sms` 实现为"**模拟通道**"：
        1. 写结构化日志（便于演示时在控制台看到"短信已发出"）；
        2. 落 Redis 队列（Key：`pending_sms`，与需求 8.4 的约定一致）；
        3. Redis 不可用时写本地 JSON 文件 `./app/data/pending_sms.json`；
        4. 返回 `{"status": "已发送(模拟)" | "待发送", ...}`。
    —— 即"写日志 + 落队列 + 返回成功/待发状态"，绝不假装真的发了短信。

邮件通道：复用 `app/ai/tool/send_email_tool.py::send_email`（真实 SMTP，配置来自 settings）。
"""
import json
import os
from datetime import datetime

from app.core.config import settings
from app.core.logger import logger

# 本地兜底队列文件（Redis 不可用时使用）
PENDING_SMS_FILE = os.path.join(".", "app", "data", "pending_sms.json")

# Redis 队列 Key（需求 8.4：模拟短信待发队列 pending_sms）
PENDING_SMS_KEY = "pending_sms"


def _save_pending_locally(item: dict) -> bool:
    """把待发短信写入本地 JSON 文件（Redis 不可用时的最后一道兜底）"""
    try:
        directory = os.path.dirname(PENDING_SMS_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        records: list = []
        if os.path.exists(PENDING_SMS_FILE):
            try:
                with open(PENDING_SMS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                records = loaded if isinstance(loaded, list) else []
            except Exception:
                records = []
        records.append(item)
        # 只保留最近 1000 条，避免文件无限增长
        records = records[-1000:]
        with open(PENDING_SMS_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"写入本地待发短信队列失败：{e}")
        return False


def _push_pending_redis(item: dict) -> bool:
    """把待发短信推入 Redis 队列；Redis 不可用时返回 False（由调用方落本地文件）"""
    try:
        import redis                                        # 可选依赖：延迟导入

        client = redis.StrictRedis(**settings.redis_client_kwargs())
        client.rpush(PENDING_SMS_KEY, json.dumps(item, ensure_ascii=False))
        return True
    except ImportError:
        logger.warning("未安装 redis，待发短信改为写本地 JSON 文件")
        return False
    except Exception as e:
        logger.warning(f"Redis 不可用，待发短信改为写本地 JSON 文件：{e}")
        return False


def send_sms(to: str, content: str) -> dict:
    """
    发送短信通知（**模拟通道**：写日志 + 落 Redis 队列，不接真实短信网关）

    参数：
        to      接收手机号
        content 短信内容
    返回：
        {"ok": bool, "status": "已发送(模拟)"|"待发送", "channel": "sms",
         "to": str, "queued": bool, "msg": str}

    为什么这样实现：
        演示/教学项目无法接入真实短信通道，但告警链路必须"闭环可验证"，
        因此把消息落到 `pending_sms` 队列 —— 既能在日志里看到告警，
        又能证明"消息确实被系统接收、可被后续真实网关消费"（FR-07 通道 1）。
    """
    to = (to or "").strip()
    content = (content or "").strip()
    if not to or not content:
        logger.warning("send_sms 参数不完整")
        return {"ok": False, "status": "参数错误", "channel": "sms", "to": to, "queued": False,
                "msg": "接收号码与短信内容都不能为空"}

    item = {
        "channel": "sms",
        "to": to,
        "content": content,
        "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "待发送",
        "note": "模拟通道：未接入真实短信网关，等待真实网关消费",
    }

    # 1) 写日志（演示时可在控制台直接看到告警短信）
    logger.warning(f"【模拟短信】收件人={to} 内容={content}")

    # 2) 落队列：Redis 优先，失败写本地 JSON
    queued = _push_pending_redis(item)
    if not queued:
        queued = _save_pending_locally(item)

    if queued:
        return {"ok": True, "status": "已发送(模拟)", "channel": "sms", "to": to,
                "queued": True, "msg": "短信已进入发送队列（模拟通道，非真实短信）"}
    # 队列也写不进去：明确返回失败，交给上层重试/人工处理
    return {"ok": False, "status": "失败", "channel": "sms", "to": to,
            "queued": False, "msg": "短信队列不可用（Redis 与本地文件均写入失败），请人工介入"}


def send_email(to: str, subject: str, content: str) -> dict:
    """
    发送邮件通知（真实 SMTP，配置来自 settings）

    参数：
        to      收件人邮箱（支持英文逗号分隔多个）
        subject 邮件主题
        content 邮件正文
    返回：
        {"ok": bool, "status": str, "channel": "email", "to": str, "msg": str}
    """
    to = (to or "").strip()
    subject = (subject or "").strip() or "健康管家通知"
    if not to:
        return {"ok": False, "status": "参数错误", "channel": "email", "to": to,
                "msg": "收件人邮箱不能为空"}

    try:
        from app.ai.tool.send_email_tool import send_email as _send_email   # 复用同一实现

        result = _send_email(to, subject, content or "")
    except Exception as e:
        logger.error(f"send_email 调用失败：{e}")
        return {"ok": False, "status": "失败", "channel": "email", "to": to, "msg": f"邮件发送异常：{e}"}

    ok = isinstance(result, str) and "成功" in result
    return {
        "ok": ok,
        "status": "已发送" if ok else "待发送",
        "channel": "email",
        "to": to,
        "msg": result if isinstance(result, str) else str(result),
    }


if __name__ == "__main__":
    # 自测：无 Redis / 无邮件配置时应优雅降级并返回结构化状态，不抛异常
    import json as _json

    print(_json.dumps(send_sms("13800000000", "您的家人心率异常，请及时关注"), ensure_ascii=False))
    print(_json.dumps(send_sms("", ""), ensure_ascii=False))
    print(_json.dumps(send_email("family@example.com", "用药提醒", "请提醒老人按时服药"), ensure_ascii=False))
