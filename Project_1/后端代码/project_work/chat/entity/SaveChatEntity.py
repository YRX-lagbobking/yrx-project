from pydantic import BaseModel,Field

class SaveChatClass(BaseModel):
    user_id: str=Field(...,title="用户名")
    parent_id: int=Field(...,title="历史记录id")
    answer: str=Field(...,title="AI的回答")
    question: str=Field(...,title="用户提问的问题")