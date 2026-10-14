"""
邮件发送工具入参模型（Pydantic）

相对 exam_0914 的改进（重要）：
    exam_0914 里 `to` 使用了 `max_length=20, min_length=10`，
    这会把 `zhangsan@example.com.cn` 之类正常邮箱直接判为非法入参（误伤）。
    这里改为**正则邮箱格式校验**：只要形如 `xxx@yyy.zzz` 即通过，
    不再限制长度，避免大模型传合法邮箱却被 Pydantic 拒绝。
"""
import re

from pydantic import BaseModel, Field, field_validator

# 邮箱格式正则（教学用简化版：本地部分@域名部分.顶级域）
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class EmailParams(BaseModel):
    """send_email_tool 的入参：收件人 + 主题 + 正文"""

    # 收件人邮箱（格式校验，不限长度）
    to: str = Field(..., description="收件人邮箱，例如 family@example.com")
    # 邮件主题
    subject: str = Field(..., description="邮件主题", min_length=1)
    # 邮件正文
    content: str = Field(..., description="邮件正文内容", min_length=1)

    @field_validator("to")
    @classmethod
    def _check_email(cls, value: str) -> str:
        """校验邮箱格式：既接受单个邮箱，也接受用英文逗号分隔的多个邮箱"""
        value = (value or "").strip()
        if not value:
            raise ValueError("收件人邮箱不能为空")
        # 支持多收件人："a@x.com,b@y.com"
        for item in value.split(","):
            item = item.strip()
            if not EMAIL_PATTERN.match(item):
                raise ValueError(f"邮箱格式不正确：{item}")
        return value

    @field_validator("subject")
    @classmethod
    def _check_subject(cls, value: str) -> str:
        """主题去空白，避免纯空格主题"""
        value = (value or "").strip()
        if not value:
            raise ValueError("邮件主题不能为空")
        return value


if __name__ == "__main__":
    # 自测：合法邮箱应通过，超长合法邮箱同样应通过（验证不再被 max_length 误伤）
    print(EmailParams(to="family@example.com", subject="测试", content="正文").model_dump())
    print(
        EmailParams(
            to="a.very.long.family.account@example.com.cn",
            subject="用药提醒",
            content="请记得服药",
        ).model_dump()
    )
    # 非法邮箱应抛异常
    try:
        EmailParams(to="not-an-email", subject="测试", content="正文")
        print("❌ 非法邮箱未被拦截")
    except Exception as e:
        print(f"✅ 非法邮箱已被拦截：{type(e).__name__}")
