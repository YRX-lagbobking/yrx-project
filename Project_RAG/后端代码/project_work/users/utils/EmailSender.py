# email_sender.py
import os
import smtplib
import random
from email.mime.text import MIMEText
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


def send_email_code(email, code):
    """发送验证码邮件，独立函数，不依赖项目其他模块"""
    try:
        sender = os.getenv("EMAIL_FROM")
        sender_pwd = os.getenv("EMAIL_PASSWORD")
        host = os.getenv("EMAIL_HOST")
        port = int(os.getenv("EMAIL_PORT", 587))

        subject = "验证码"
        content = f"您的验证码是：{code}，有效期60秒。"

        msg = MIMEText(content, 'plain', 'utf-8')
        msg['From'] = sender
        msg['To'] = email
        msg['Subject'] = subject

        # 使用已验证的发送逻辑
        smtp = smtplib.SMTP(host=host, port=port, timeout=10)
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(sender, sender_pwd)
        smtp.sendmail(sender, [email], msg.as_string())
        smtp.quit()

        print(f"邮件发送成功到 {email}")
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}")
        return False

if __name__ == '__main__':
    send_email_code("2779691651@qq.com", "1")