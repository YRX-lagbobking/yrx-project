from pydantic import BaseModel,Field
class LoginClass(BaseModel):
    email: str = Field(..., title="邮箱名"),
    password: str = Field(..., title="密码")