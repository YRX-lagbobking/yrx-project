from pydantic import BaseModel,Field
class UsernameClass(BaseModel):
    old_username:str
    new_username:str

class PasswordClass(BaseModel):
    username:str
    password:str