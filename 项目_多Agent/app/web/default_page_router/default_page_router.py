"""
默认页路由（登录入口）

职责：
    GET / → 302 重定向到 /static/login.html（静态目录由 app/main.py 挂载 ./app/html → /static）

设计说明：
    本接口不需要鉴权（契约第 18 节），是用户访问系统的第一跳；
    重定向而非直接返回 HTML，是为了让前端页面统一走静态目录，便于后续换页面不改后端。
"""
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

default_page_router = APIRouter()


@default_page_router.get("/")
async def default_page() -> RedirectResponse:
    """根路径重定向到登录页"""
    return RedirectResponse(url="/static/login.html")


# ---------------- 调用示例 ----------------
# 浏览器访问 http://localhost:8000/ → 自动跳到 http://localhost:8000/static/login.html
# curl -I http://localhost:8000/     → HTTP/1.1 307 Temporary Redirect, location: /static/login.html
