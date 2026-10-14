"""
家属通知任务（**只发邮件**）—— 短信通道已删除

Celery 为必装依赖（Redis 作 broker / backend），不做降级：

    1. `notify_family`（Celery 任务）：Worker 进程内真正发邮件（真实 SMTP）；
       失败按 `max_retries=3, countdown=60s` 重试，重试耗尽写日志并返回失败结果。
    2. `notify_family_async`（主流程入口）：`notify_family.delay(...)` 投递到 Celery，立即返回。

说明：为兼容旧调用点，两个函数仍保留 `channel` 形参，但**内部一律走邮件**。

启动（Windows 必须 -P solo）：
    celery -A app.core.celery_app.celery_app worker -l info -P solo
"""
from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.logger import logger
from app.core.security import mask_email

# 重试策略（契约 max_retries=3 / countdown=60）
MAX_RETRIES = 3
RETRY_COUNTDOWN = 60
CHANNEL = "email"


def _resolve_email(user_id: str) -> str:
    """收件邮箱：紧急联系人邮箱 → 绑定家属邮箱 → settings.email_to 兜底；找不到返回空串"""
    from app.dao.user_dao import UserDAO

    contact = UserDAO.get_emergency_contact(user_id) or {}
    family = UserDAO.list_family(user_id) or []

    candidates = [contact.get("email")]
    candidates += [item.get("email") for item in family if isinstance(item, dict)]
    candidates.append(settings.email_to)

    for candidate in candidates:
        if candidate:
            return str(candidate).strip()
    return ""


def _send_email(to: str, user_id: str, level: str, content: str) -> dict:
    """
    发预警邮件（app 层真实 SMTP 实现，返回统一 {"ok","msg"}）

    为什么不用 mcp_server.tools.notify_tool：
        那个函数只是包装本实现；而 Celery worker 执行任务时，cwd 已不在 sys.path，
        顶层包 mcp_server 会 import 失败（ModuleNotFoundError）。用 app.* 则始终可用。
    """
    from app.ai.tool.send_email_tool import send_email

    result = send_email(to, f"【健康预警·{level}】老人 {user_id} 健康异常提醒", content)
    text = result if isinstance(result, str) else str(result)
    return {"ok": "成功" in text, "msg": text}


@celery_app.task(bind=True, max_retries=MAX_RETRIES, default_retry_delay=RETRY_COUNTDOWN, name="notify_family")
def notify_family(self, user_id: str, level: str, content: str, channel: str = CHANNEL) -> dict:
    """
    家属预警邮件任务（Celery Worker 执行）

    channel 参数仅为兼容旧调用点保留，实际只发邮件。
    返回：{"ok": bool, "channel": "email", "msg": str}
    """
    logger.info(f"[通知任务] user_id={user_id} level={level} content={content[:60]}")
    to = _resolve_email(user_id)
    if not to:
        msg = "未找到家属邮箱（未绑定家属且无紧急联系人）"
        logger.error(f"[通知任务] {msg}：user_id={user_id}")
        return {"ok": False, "channel": CHANNEL, "msg": msg}

    result = _send_email(to, user_id, level, content)
    failed = isinstance(result, dict) and result.get("ok") is False
    if failed:
        errors = f"email: {result.get('msg') or result}"
        if self.request.retries < MAX_RETRIES:
            logger.warning(
                f"[通知任务] 邮件发送失败，{RETRY_COUNTDOWN}s 后进行第 "
                f"{self.request.retries + 1} 次重试：{errors}"
            )
            raise self.retry(exc=RuntimeError(errors), countdown=RETRY_COUNTDOWN)
        logger.error(f"[通知任务] 邮件重试 {MAX_RETRIES} 次仍失败：{errors}")
        return {"ok": False, "channel": CHANNEL, "msg": f"邮件发送失败（已重试 {MAX_RETRIES} 次）：{errors}"}

    masked = mask_email(to)
    logger.info(f"通知发送成功：channel=email to={masked} user_id={user_id}")
    return {"ok": True, "channel": CHANNEL, "msg": f"预警邮件已发送给 {masked}"}


def notify_family_async(user_id, level, content, channel: str = CHANNEL) -> dict:
    """
    主流程调用入口：投递 Celery 异步任务后立即返回（不等待执行结果）

    真正的邮件发送由 Worker 完成：
        celery -A app.core.celery_app.celery_app worker -l info -P solo
    """
    notify_family.delay(user_id, level, content, CHANNEL)
    logger.info(f"通知任务已提交 Celery：user_id={user_id} level={level} channel={CHANNEL}")
    return {"ok": True, "channel": CHANNEL, "msg": "已提交 Celery 异步通知任务"}


if __name__ == "__main__":
    logger.info(f"Celery broker/backend：{settings.redis_url}")
    logger.info(f"u001 收件邮箱解析 = {_resolve_email('u001') or '（未找到）'}")
    logger.info(f"投递结果：{notify_family_async('u001', '高危', '【高危预警】心率达到 140 次/分，请及时关注。')}")
    logger.info("提示：真正发送需要 worker 进程（-P solo）")
