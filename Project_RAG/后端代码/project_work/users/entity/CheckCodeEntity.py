from pydantic import BaseModel,Field
class CheckCodeClass(BaseModel):
    email :str
    code:str