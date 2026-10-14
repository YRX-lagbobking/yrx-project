"""
聊天路由（文本入口）

职责：
    1. POST /create_session：创建 / 校验会话（会话归属校验在 HealthGraphAgent 内完成）；
    2. GET  /chat：SSE 流式问询，事件格式与 exam_0914 完全一致；
    3. POST /chat/skill：指定 Skill 直接触发（如 medication_reminder 用药提醒邮件）。

设计说明：
    1. 图智能体统一从 request.app.state.health_agent 取（main.py 的 lifespan 注入）；
    2. 本模块只做"参数校验 + 鉴权 + 流式透传"，任何业务判断都下沉到图节点，避免路由层膨胀；
    3. 鉴权统一用 app/core/security.py 的 get_current_user（Authorization: Bearer <token>）。
"""
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.logger import logger
from app.core.security import TokenData, get_current_user

chat_router = APIRouter()


class SessionSchema(BaseModel):
    """创建会话入参：会话 id 与用户 id 都可为空（为空即新建）"""

    user_id: str | None = Field(default=None, description="用户 ID，为空时按当前登录用户处理")
    session_id: str | None = Field(default=None, description="会话 ID，携带时会校验归属")


class SkillSchema(BaseModel):
    """Skill 触发入参：技能名 + 用户原始请求"""

    skill_name: str = Field(..., description="技能名，如 medication_reminder / weekly_report")
    question: str = Field(..., description="用户原始请求文本")
    user_id: str | None = Field(default=None, description="用户 ID")
    session_id: str | None = Field(default=None, description="会话 ID")
    role: str = Field(default="elder", description="调用角色：elder / family / admin")


def _sse(payload: dict) -> str:
    """统一封装 SSE 事件文本（ensure_ascii=False 保证中文原样下发）"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@chat_router.post("/create_session")
async def create_session(request: Request, session: SessionSchema, user: TokenData = Depends(get_current_user)):
    """
    创建 / 校验会话

    返回风格与 exam_0914 一致：{"code":200,"msg":...,"data":session_id}
    """
    health_agent = request.app.state.health_agent
    # 未携带 user_id 时按当前登录用户处理（避免前端遗漏导致会话归属为空）
    user_id = session.user_id or user.user_id
    logger.info(f"创建会话请求：user_id={user_id}, session_id={session.session_id}")

    # 前端没带 session_id，直接建新的
    if not session.session_id:
        new_id = await health_agent.create_session(user_id)
        return {"code": 200, "msg": "新建会话", "data": new_id}

    # 带 session_id：先校验存在性与归属（不存在或不属于该用户则新建）
    is_exist, msg = await health_agent.check_session(user_id, session.session_id)
    if not is_exist:
        logger.warning(f"会话不可用（{msg}），改为新建会话")
        new_id = await health_agent.create_session(user_id)
        return {"code": 200, "msg": f"{msg}，已新建会话", "data": new_id}

    logger.info(f"会话存在且归属校验通过：{session.session_id}")
    return {"code": 200, "msg": "会话存在", "data": session.session_id}


@chat_router.get("/chat")
async def chat(
    request: Request,
    question: str = Query(..., description="用户问题"),
    user_id: str = Query(..., description="用户 ID"),
    session_id: str = Query(..., description="会话 ID"),
    role: str = Query(default="elder", description="调用角色：elder / family / admin"),
    user: TokenData = Depends(get_current_user),
):
    """
    SSE 流式问询

    事件格式（与 exam_0914 一致）：
        片段：data: {"data": "片段", "done": false}\\n\\n
        结束：data: {"data": "", "done": true}\\n\\n
        异常：data: {"data": "流式异常", "done": true}\\n\\n
    """
    health_agent = request.app.state.health_agent
    # 以 Token 中的身份为准，防止前端伪造 role 越权（路由层做一次粗粒度兜底）
    real_role = user.role or role or "elder"
    logger.info(f"聊天请求：question={question}, user_id={user_id}, session_id={session_id}, role={real_role}")

    async def generate():
        """把图智能体的异步生成器转成 SSE 事件流"""
        try:
            # 图智能体 chat 签名为 (question, user_id, session_id, role)
            async for x in health_agent.chat(question, user_id, session_id, real_role):
                # done=False 表示流式未结束
                yield _sse({"data": x, "done": False})
            # 流式结束
            yield _sse({"data": "", "done": True})
        except Exception as e:
            # 流式异常也必须发一条终止事件，否则前端打字机不停止
            logger.error(f"流式异常：{e}")
            yield _sse({"data": "流式异常", "done": True})

    return StreamingResponse(generate(), media_type="text/event-stream")


@chat_router.post("/chat/skill")
async def chat_skill(request: Request, skill: SkillSchema, user: TokenData = Depends(get_current_user)):
    """指定 Skill 触发（如"帮我给张三发一封用药提醒邮件"），同样以 SSE 返回"""
    health_agent = request.app.state.health_agent
    user_id = skill.user_id or user.user_id
    real_role = user.role or skill.role or "elder"
    logger.info(f"技能请求：skill_name={skill.skill_name}, user_id={user_id}, session_id={skill.session_id}")

    async def generate():
        """把 chat_skill 的异步生成器转成 SSE 事件流"""
        try:
            # 图智能体 chat_skill 签名为 (skill_name, question, user_id, session_id, role)
            async for x in health_agent.chat_skill(skill.skill_name, skill.question, user_id, skill.session_id, real_role):
                yield _sse({"data": x, "done": False})
            yield _sse({"data": "", "done": True})
        except Exception as e:
            logger.error(f"技能流式异常：{e}")
            yield _sse({"data": "流式异常", "done": True})

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------- 历史记录（前端左侧列表 + 点开回溯） ----------------
@chat_router.get("/sessions")
async def list_sessions_api(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200, description="最多返回条数"),
    user: TokenData = Depends(get_current_user),
):
    """
    历史会话列表（前端"历史记录"的数据源）

    数据源：PostgreSQL chat_session 表（每轮对话后刷新 update_time）。
    降级：PostgreSQL 不可用时 data 为 []，前端保留内存列表，对话链路不受影响。
    """
    from app.dao import session_dao

    rows = await session_dao.list_sessions(user.user_id, limit)
    logger.info(f"历史会话列表：user_id={user.user_id} 条数={len(rows)}")
    return {"code": 200, "msg": "ok", "data": rows, "total": len(rows)}


@chat_router.get("/session/history")
async def session_history(
    request: Request,
    session_id: str = Query(..., description="会话 ID"),
    user: TokenData = Depends(get_current_user),
):
    """
    取某会话的历史消息（前端点开某条历史时渲染）

    数据源：PostgreSQL chat_message 表（只存用户可见对话）；
    表里没有（老会话 / 表未就绪）时回退 LangGraph Checkpointer；
    归属校验：优先 PostgreSQL 会话索引，索引缺失时退回 Redis 会话校验。
    """
    from app.dao import session_dao

    health_agent = request.app.state.health_agent
    row = await session_dao.get_session(session_id)
    title = (row or {}).get("title") or "历史对话"
    if not row or str(row.get("user_id")) != str(user.user_id):
        ok, reason = await health_agent.check_session(user.user_id, session_id)
        if not ok:
            logger.warning(f"历史记录会话不可用：session_id={session_id} user_id={user.user_id}（{reason}）")
            return {"code": 403, "msg": f"会话不存在或不属于当前用户（{reason}）",
                    "data": {"session_id": session_id, "title": "", "messages": []}}
        title = "历史对话"

    rows = await session_dao.list_messages(session_id)
    if rows:
        messages = [{
            "role": "user" if str(r.get("role") or "").lower() in ("user", "human") else "bot",
            "content": str(r.get("content") or ""),
        } for r in rows]
    else:
        messages = await health_agent.load_history(session_id)      # 老会话兜底（Checkpointer）
    logger.info(f"历史记录：session_id={session_id} 消息数={len(messages)}")
    return {"code": 200, "msg": "ok",
            "data": {"session_id": session_id, "title": title, "messages": messages}}


@chat_router.delete("/session")
async def delete_session_api(
    request: Request,
    session_id: str = Query(..., description="会话 ID"),
    user: TokenData = Depends(get_current_user),
):
    """删除会话：会话索引 + 该会话历史消息 + Redis 会话标记（前端历史列表的删除按钮调它）"""
    from app.dao import session_dao

    health_agent = request.app.state.health_agent
    row = await session_dao.get_session(session_id)
    if not row or str(row.get("user_id")) != str(user.user_id):
        ok, reason = await health_agent.check_session(user.user_id, session_id)
        if not ok:
            return {"code": 403, "msg": f"会话不存在或不属于当前用户（{reason}）", "data": None}

    await session_dao.delete_session(session_id, user.user_id)
    await session_dao.delete_messages(session_id)
    try:
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            await redis.delete(session_id)          # 会话标记的 key 就是 session_id
    except Exception as e:  # noqa: BLE001
        logger.warning(f"清理 Redis 会话标记失败：{type(e).__name__}: {e}")
    logger.info(f"会话已删除：session_id={session_id} user_id={user.user_id}")
    return {"code": 200, "msg": "已删除", "data": {"session_id": session_id}}


# ---------------- 调用示例 ----------------
# 1) 建会话（需先登录拿 token）
#    curl -X POST http://localhost:8000/create_session -H "Authorization: Bearer <token>" \
#         -H "Content-Type: application/json" -d '{"user_id":"u001","session_id":null}'
# 2) SSE 流式问询
#    curl -N "http://localhost:8000/chat?question=我今天血压怎么样&user_id=u001&session_id=session:xxx" \
#         -H "Authorization: Bearer <token>"
# 3) 指定技能
#    curl -N -X POST http://localhost:8000/chat/skill -H "Authorization: Bearer <token>" \
#         -H "Content-Type: application/json" \
#         -d '{"skill_name":"medication_reminder","question":"帮我给张三发一封用药提醒邮件","user_id":"u001","session_id":"session:xxx"}'
