"""
邮件发送工具（LangChain Tool + 普通函数双出口）

为什么要有两个出口（契约第 9 节）：
    1. `send_email(to, subject, content)`：**普通函数**，供 Celery 通知任务、
       MCP Server 的 notify_tool、脚本直接 import 调用，不带 LangChain 包装；
    2. `send_email_tool(...)`：`@tool("send_email_tool", args_schema=EmailParams)`，
       给大模型（Agent）调用。
    两者内部复用同一套实现 `_do_send_email`，避免"工具能发、异步任务发不出"的割裂。

配置来源（与 exam_0914 的关键差异）：
    exam_0914 直接 `os.getenv("EMAIL_HOSt")`（还拼错了 host），
    这里统一走 `from app.core.config import settings`：
        settings.email_host / email_port / email_to / email_password。

容错（契约第 0 节：工具永不抛异常给上层）：
    配置缺失、SMTP 认证失败、网络超时，一律返回中文提示字符串，不抛异常。
"""
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from langchain.tools import tool

from app.ai.tool.schema.send_email_schema import EmailParams
from app.core.config import settings
from app.core.logger import logger


def _do_send_email(to: str, subject: str, content: str) -> str:
    """
    邮件发送的真实实现（普通函数与 LangChain Tool 共用）

    返回中文结果字符串：
        "邮件发送成功" / "邮件配置未完成…" / "邮件发送失败：<原因>"
    """
    # ---------- 1. 参数兜底 ----------
    to = (to or "").strip()
    subject = (subject or "").strip()
    content = content or ""
    if not to:
        return "邮件发送失败：收件人邮箱为空"
    if not subject:
        subject = "健康管家通知"        # 主题缺失时给默认主题，不阻断通知链路

    # ---------- 2. 配置检查（全部来自 settings，禁止 os.getenv） ----------
    sender = (settings.email_to or "").strip()
    host = (settings.email_host or "").strip()
    password = settings.email_password or ""
    if not sender or not host or not password:
        logger.error("邮件配置不完整（EMAIL_TO / EMAIL_HOST / EMAIL_PASSWORD）")
        return "邮件配置未完成，请在 .env 中配置 EMAIL_TO、EMAIL_HOST、EMAIL_PASSWORD"

    try:
        port = int(settings.email_port)
    except (TypeError, ValueError):
        port = 465

    try:
        # ---------- 3. 组装邮件 ----------
        msg = MIMEText(content, "plain", "utf-8")          # 正文 + 中文编码 UTF-8
        msg["To"] = to
        msg["Subject"] = Header(subject, "utf-8")          # 中文主题需 Header 编码
        msg["From"] = formataddr((str(Header("智能健康管家", "utf-8")), sender))

        # ---------- 4. 发送（465 走 SSL，587/25 走 STARTTLS） ----------
        receivers = [x.strip() for x in to.split(",") if x.strip()]
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                smtp.login(sender, password)
                smtp.sendmail(sender, receivers, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls()
                smtp.login(sender, password)
                smtp.sendmail(sender, receivers, msg.as_string())

        logger.info(f"邮件已发送至 {receivers}，主题：{subject}")
        return "邮件发送成功"
    except Exception as e:
        # 通知失败不能让主流程崩：记录日志 + 返回可读原因（由上层落待发队列重试）
        logger.error(f"邮件发送异常：{e}")
        return f"邮件发送失败：{e}"


# 普通函数出口（Celery / MCP notify_tool / 脚本复用）
def send_email(to: str, subject: str, content: str) -> str:
    """
    发送邮件（普通函数版）

    参数：
        to      收件人邮箱，多个收件人用英文逗号分隔
        subject 邮件主题
        content 邮件正文（纯文本）
    返回：
        "邮件发送成功" 或 以 "邮件" 开头的中文失败原因（不抛异常）
    """
    return _do_send_email(to, subject, content)


@tool("send_email_tool", args_schema=EmailParams)
def send_email_tool(to: str, subject: str, content: str) -> str:
    """
    发送邮件通知（发邮件、发消息给家属）

    使用场景：
        - 高危告警通知家属（FR-07 通道 1）
        - 用药提醒邮件（FR-12 medication_reminder 技能）
        - 日报/周报邮件推送（FR-08）

    参数：
        to      收件人邮箱（支持英文逗号分隔多个）
        subject 邮件主题
        content 邮件正文
    """
    return _do_send_email(to, subject, content)


if __name__ == "__main__":
    # 自测：无 .env 配置时应优雅返回"配置未完成"，而不是抛异常
    print("普通函数出口：", send_email("family@example.com", "自测主题", "这是一封自测邮件"))
    print("工具出口：", send_email_tool.invoke(
        {"to": "family@example.com", "subject": "自测主题", "content": "这是一封自测邮件"}
    ))
    # 入参校验演示：非法邮箱会被 Pydantic 拦截
    print("邮箱格式为合理的格式（19 字符）：", EmailParams(
        to="elder.family@example.com", subject="测试", content="正文"
    ).to)
