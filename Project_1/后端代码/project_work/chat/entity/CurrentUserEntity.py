from pydantic import BaseModel,Field

class CurrentUserClass(BaseModel):
    user_id:int
    username:str
    role:str
