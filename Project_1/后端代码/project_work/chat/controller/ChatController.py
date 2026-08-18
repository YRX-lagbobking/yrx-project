import json

from fastapi import APIRouter,Depends,Request
from starlette.responses import StreamingResponse

from chat.service import ChatService
from chat.entity.ChatEntity import  ChatClass
from chat.entity.CurrentUserEntity import CurrentUserClass
from chat.entity.SaveChatEntity import SaveChatClass
from common import JWTDecoder
from common.JWTDecoder import get_current_user

chat_router=APIRouter()
@chat_router.post(
    path="/chat",
    summary="聊天",
    description=""""
    请求的路径：http://localhost:8000/chat/chat，
    请求的参数：用户的问题
    返回的参数：{
        code:为状态码200成功，500失败
        msg:提示的信息
        data:返回的聊天内容
    }流式输出SSE
    """
)
def chat(request:Request,chatclass:ChatClass,current_user:CurrentUserClass = Depends(get_current_user)):
    print(f"chat认证通过：{current_user}")
    def generator():
        for chunk in ChatService.chat(request,chatclass):
            yield f"data:{json.dumps({'content':chunk})}\n\n"
        yield f"data:{json.dumps({'content':'done'})}\n\n"
    return StreamingResponse(
        generator(),
        media_type="text/event-stream")

@chat_router.post(
    path="/saveChat",
    summary="聊天",
    description=""""
    请求的路径：http://localhost:8000/chat/chat，
    请求的参数：用户的问题
    返回的参数：{
        code:为状态码200成功，500失败
        msg:提示的信息
        data:返回的聊天内容
    }流式输出SSE
    """
)
def save_chat(savechat:SaveChatClass,current_user:CurrentUserClass = Depends(get_current_user)):
    print(f"save_chat认证通过：{current_user}")
    return ChatService.save_chat(savechat)
