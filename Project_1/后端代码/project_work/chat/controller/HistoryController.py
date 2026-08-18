from chat.entity.DeleteHistoryEntity import DeleteHistoryClass
from chat.entity.CurrentUserEntity import CurrentUserClass
from chat.service import HistoryService
from fastapi import APIRouter, Depends
from common.JWTDecoder import get_current_user
from common.RolePermission import has_roles

#-----------历史记录栏----------
history_router=APIRouter()
@history_router.get(
    path="/queryHistoryMenu",
    summary="查询历史记录菜单栏",
)
def query_history_menu(user_id,current_user:CurrentUserClass = Depends( get_current_user)):
    print(f"query_history_menu认证通过：{current_user}")
    return HistoryService.query_history_menu(user_id)
#--------查询某一条历史记录-----------
@history_router.get(
    path="/conversationLog",#驼峰命名
    summary="查询某一条历史记录",
)
def conversation_log(history_id,current_user:CurrentUserClass = Depends( get_current_user)):
    print(f"history_id认证通过：{current_user}")
    return HistoryService.conversation_log(history_id)
#---------删除某一条历史记录--------------
@history_router.post(
    path="/deleteHistory",#驼峰命名
    summary="删除某一条历史记录",
)
def delete_history(deleteHistory: DeleteHistoryClass,user: CurrentUserClass = Depends(has_roles(["admin"]))):
    print(f"delete_history认证通过：{user}")
    return HistoryService.delete_history(deleteHistory)
#---------历史记录搜索------------------
@history_router.get(
    path="/searchMessages",#驼峰命名
    summary="searchMessages",
)
def search_messages(user_id:str,keyword:str,current_user:CurrentUserClass = Depends( get_current_user)):
    print(f"search_messages认证通过：{current_user}")
    return HistoryService.search_messages(user_id,keyword)