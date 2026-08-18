from fastapi import FastAPI
from ai import LoadChroma,LoadLLM,LoadReranker
from users.controller.UsersController import users_router
from chat.controller.ChatController import chat_router
from chat.controller.HistoryController import history_router
from contextlib import asynccontextmanager
import uvicorn as uv
class Model():
    def __init__(self):
        print("初始化,加载模型")
        # 创建检索器对象retriver:
        self.vector=LoadChroma.load_chroma().as_retriever(search_kwargs={"k": 15})
        self.vector_2=LoadChroma.load_chroma_2().as_retriever(search_kwargs={"k": 15})
        self.llm=LoadLLM.load_llm()
        self.reranker=LoadReranker.load_reranker()
        self.chromadb_1=LoadChroma.load_chroma()
        self.chromadb_2=LoadChroma.load_chroma_2()
        print("✅ 所有模型加载完成！")
@asynccontextmanager
async def start_end_run(app):
    """
        app：FastAPI 对象
        yield 之前的内容：启动项目执行
        yield 之后的内容：关闭项目执行
        如果想实现一些变量的初始化操作，可以把变量写入到 app.state 属性中，格式如下：
            app.state.变量名 = 初始化值
        如果某个接口中需要使用，就用 request 对象取出来，格式如下
            变量名2 = request.app.state.变量名
    """
    # app.state.username = "cc"
    print("启动项目")
    app.state.models=Model()
    yield
    print("关闭项目")


# 创建应用
app = FastAPI(lifespan=start_end_run)



# 注册子路由对象
app.include_router(
    router=users_router,
    prefix="/users",
    tags=["users"]
)

app.include_router(
    router=chat_router,
    prefix="/chat",
    tags=["chat"]
)

app.include_router(
    router=history_router,
    prefix="/chat",
    tags=["chat"]
)
#跨域处理
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],#允许的域名---完整的客户端域名
    allow_credentials=True,#允许携带cookie
    allow_methods=["*"],#允许的请求方式
    allow_headers=["*"],#允许的请求头
)
if __name__ == '__main__':
    uv.run(
        app="main:app",
        host="localhost",
        port=8000,
        reload=False
    )