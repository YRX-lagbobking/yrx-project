from pydantic import BaseModel,Field

class DeleteHistoryClass(BaseModel):
    history_id: int = Field(..., title="历史记录id")
    user_id: str = Field(..., title="用户名")