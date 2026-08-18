import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv
import random

# 加载环境变量
load_dotenv()


def test_send_email():
    print("=" * 50)
    print("开始测试邮件发送")
    print("=" * 50)

    try:
        # 1. 检查环境变量
        print("\n1. 检查环境变量...")
        sender = os.getenv("EMAIL_FROM")
        sender_pwd = os.getenv("EMAIL_PASSWORD")
        host = os.getenv("EMAIL_HOST")
        port = os.getenv("EMAIL_PORT")

        print(f"   EMAIL_HOST: {host}")
        print(f"   EMAIL_PORT: {port}")
        print(f"   EMAIL_FROM: {sender}")
        print(f"   EMAIL_PASSWORD: {'*' * len(sender_pwd) if sender_pwd else '未设置'}")

        if not all([sender, sender_pwd, host, port]):
            print("❌ 环境变量不完整")
            return

        # 2. 创建邮件
        print("\n2. 创建邮件内容...")
        code = ''.join(random.choices('0123456789', k=4))
        content = f"您的验证码是：{code}，有效期60秒"

        msg = MIMEText(content, 'plain', 'utf-8')
        msg['From'] = sender
        msg['To'] = '2779691651@qq.com'  # 测试邮箱
        msg['Subject'] = '测试验证码'

        # 3. 连接 SMTP 服务器
        print(f"\n3. 连接 SMTP 服务器 {host}:{port}...")

        # ✅ 方法一：使用 SMTP + TLS（推荐）
        print("   尝试方式一：SMTP + TLS...")
        try:
            smtp = smtplib.SMTP(host=host, port=int(port), timeout=10)
            print("   ✅ SMTP 连接成功")

            print("   发送 EHLO...")
            smtp.ehlo()
            print("   ✅ EHLO 成功")

            print("   启用 TLS...")
            smtp.starttls()
            print("   ✅ TLS 启用成功")

            print("   重新发送 EHLO...")
            smtp.ehlo()
            print("   ✅ EHLO 成功")

            print("   登录...")
            smtp.login(sender, sender_pwd)
            print("   ✅ 登录成功")

            print("   发送邮件...")
            smtp.sendmail(sender, '2779691651@qq.com', msg.as_string())
            print("   ✅ 邮件发送成功")

            smtp.quit()
            print("\n🎉 邮件发送成功！")
            return True

        except Exception as e1:
            print(f"   ❌ 方式一失败: {e1}")

            # ✅ 方法二：使用 SMTP_SSL（备选）
            print("\n   尝试方式二：SMTP_SSL...")
            try:
                import ssl
                context = ssl.create_default_context()
                smtp = smtplib.SMTP_SSL(
                    host=host,
                    port=465,  # SSL 端口
                    context=context,
                    timeout=10
                )
                print("   ✅ SSL 连接成功")

                print("   登录...")
                smtp.login(sender, sender_pwd)
                print("   ✅ 登录成功")

                print("   发送邮件...")
                smtp.sendmail(sender, '2779691651@qq.com', msg.as_string())
                print("   ✅ 邮件发送成功")

                smtp.quit()
                print("\n🎉 邮件发送成功！")
                return True

            except Exception as e2:
                print(f"   ❌ 方式二失败: {e2}")

                # ✅ 方法三：使用 25 端口（最后尝试）
                print("\n   尝试方式三：端口 25...")
                try:
                    smtp = smtplib.SMTP(host=host, port=25, timeout=10)
                    print("   ✅ 连接成功")

                    smtp.ehlo()
                    smtp.starttls()
                    smtp.ehlo()
                    smtp.login(sender, sender_pwd)
                    smtp.sendmail(sender, '2779691651@qq.com', msg.as_string())
                    smtp.quit()
                    print("\n🎉 邮件发送成功！")
                    return True

                except Exception as e3:
                    print(f"   ❌ 方式三失败: {e3}")
                    raise

    except Exception as e:
        print(f"\n❌ 邮件发送异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    test_send_email()