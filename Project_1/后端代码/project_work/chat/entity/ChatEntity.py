from pydantic import BaseModel,Field

class ChatClass(BaseModel):
    history_id:int
    question:str

