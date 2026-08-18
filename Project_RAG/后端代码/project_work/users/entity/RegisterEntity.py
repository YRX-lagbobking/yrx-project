from pydantic import BaseModel,Field
class RegisterClass(BaseModel):
    email: str
    username: str
    password: str