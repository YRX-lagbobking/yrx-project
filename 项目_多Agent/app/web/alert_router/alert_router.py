"""
告警路由（列表查询 / 告警确认 / 手动检测）

职责：
    1. GET  /alert/list            ：按角色过滤告警列表（老人只看自己，家属看绑定老人，管理员不限）；
    2. POST /alert/{alert_id}/confirm：告警确认闭环（先校验归属，越权 403）；
    3. POST /alert/detect          ：手动触发三层检测（**无副作用**，AC-10）。

设计说明（重要）：
    1. 手动检测接口只调用纯计算层 detect_anomaly / risk_level_of，**绝不写 alert_record、绝不发通知**，
       否则老人主动问询会污染自动检测的统计口径（FR-06 / 10.3 节）；
    2. detect_anomaly、DAO、rag_search_with_fallback 都是同步函数，统一用 asyncio.to_thread / 直接调用处理，
       避免阻塞事件循环（生产应改为异步连接池）。
"""
import asyncio

from fastapi import APIRouter, Depends, Query
from starlette.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.logger import logger
from app.core.security import TokenData, can_access_user, get_current_user

alert_router = APIRouter()


class ConfirmSchema(BaseModel):
    """告警确认入参"""

    status: str = Field(default="已确认", description="确认后的状态，如 已确认 / 已忽略")


class DetectSchema(BaseModel):
    """手动检测入参"""

    user_id: str = Field(..., description="目标老人 user_id")


async def _bound_elders(current: TokenData) -> list[str]:
    """
    取家属绑定的老人列表（DAO 同步 → 线程池）

    老人/管理员不需要绑定列表，直接返回空列表。
    """
    if current.role != "family":
        return []
    try:
        from app.dao.user_dao import UserDAO

        elders = await asyncio.to_thread(UserDAO.list_bound_elders, current.user_id)
        return [str(x) for x in (elders or [])]
    except Exception as e:
        logger.error(f"查询绑定老人失败：{e}")
        return []


async def _can_access(current: TokenData, target_user_id: str) -> bool:
    """统一的数据权限判定：admin 全通、elder 仅本人、family 仅绑定老人"""
    bound = await _bound_elders(current)
    try:
        return can_access_user(current, str(target_user_id), bound)
    except Exception as e:
        logger.error(f"权限判定异常，按拒绝处理：{e}")
        return False


@alert_router.get("/alert/list")
async def alert_list(
    user_id: str | None = Query(default=None, description="目标 user_id；家属/老人可省略"),
    limit: int = Query(default=20, ge=1, le=200, description="返回条数上限"),
    current: TokenData = Depends(get_current_user),
):
    """
    告警列表（按角色过滤）

    - elder ：只能查自己（目标不是自己 → 403）
    - family：查全部已绑定老人（可传 user_id 缩小范围，未绑定 → 403）
    - admin ：可指定任意 user_id，不传则返回全部老人的告警
    """
    logger.info(f"告警列表请求：current={current.user_id}, role={current.role}, target={user_id}, limit={limit}")
    try:
        from app.dao.alert_dao import AlertDAO
        from app.dao.user_dao import UserDAO

        # ---------------- elder：只能查自己 ----------------
        if current.role == "elder":
            target = str(user_id or current.user_id)
            if target != str(current.user_id):
                logger.warning(f"老人越权查询他人告警被拒绝：{current.user_id} -> {target}")
                return JSONResponse(status_code=403, content={"code": 403, "msg": "无权查看他人告警", "data": None})
            rows = await asyncio.to_thread(AlertDAO.list_by_user, target, limit)
            return {"code": 200, "msg": "查询成功", "data": rows or []}

        # ---------------- family：已绑定老人 ----------------
        if current.role == "family":
            elders = await _bound_elders(current)
            if user_id:
                target = str(user_id)
                if not await _can_access(current, target):
                    logger.warning(f"家属越权查询未绑定老人告警被拒绝：{current.user_id} -> {target}")
                    return JSONResponse(status_code=403, content={"code": 403, "msg": "无权查看该老人告警", "data": None})
                rows = await asyncio.to_thread(AlertDAO.list_by_user, target, limit)
                return {"code": 200, "msg": "查询成功", "data": rows or []}
            if not elders:
                logger.info(f"家属 {current.user_id} 尚未绑定老人")
                return {"code": 200, "msg": "尚未绑定老人", "data": []}
            rows = await asyncio.to_thread(AlertDAO.list_by_elders, elders, limit)
            return {"code": 200, "msg": "查询成功", "data": rows or []}

        # ---------------- admin：任意 user_id，不传则汇总全部老人 ----------------
        if user_id:
            rows = await asyncio.to_thread(AlertDAO.list_by_user, str(user_id), limit)
            return {"code": 200, "msg": "查询成功", "data": rows or []}
        elders = await asyncio.to_thread(UserDAO.list_elders)
        if not elders:
            return {"code": 200, "msg": "没有老人用户", "data": []}
        rows = await asyncio.to_thread(AlertDAO.list_by_elders, [str(x) for x in elders], limit)
        return {"code": 200, "msg": "查询成功", "data": rows or []}
    except Exception as e:
        logger.error(f"告警列表查询异常：{e}")
        return {"code": 500, "msg": "告警查询失败", "data": []}


@alert_router.post("/alert/{alert_id}/confirm")
async def alert_confirm(
    alert_id: int,
    payload: ConfirmSchema | None = None,      # ★ 请求体可选：前端不传 body 时用默认状态
    current: TokenData = Depends(get_current_user),
):
    """
    告警确认（闭环）

    必须先校验该告警归属当前用户或其绑定老人，越权返回 403。
    """
    status_text = (payload.status if payload else "") or "已确认"
    logger.info(f"告警确认请求：alert_id={alert_id}, status={status_text}, current={current.user_id}")
    try:
        from app.dao.alert_dao import AlertDAO

        # 1. 先取出该告警，校验归属（越权 403）
        alert = None
        if current.role == "elder":
            # 老人在自己的告警列表里找这条记录（DAO 层已按 user_id 过滤）
            rows = await asyncio.to_thread(AlertDAO.list_by_user, str(current.user_id), 200)
            alert = next((r for r in (rows or []) if str(r.get("id") or r.get("alert_id")) == str(alert_id)), None)
        elif current.role == "family":
            elders = await _bound_elders(current)
            if elders:
                rows = await asyncio.to_thread(AlertDAO.list_by_elders, elders, 200)
                alert = next((r for r in (rows or []) if str(r.get("id") or r.get("alert_id")) == str(alert_id)), None)

        if alert is None:
            logger.warning(f"告警不属于当前用户，拒绝确认：alert_id={alert_id}, current={current.user_id}")
            return JSONResponse(status_code=403, content={"code": 403, "msg": "无权确认该告警", "data": None})

        # 2. 校验通过 → 落库确认（DAO 同步 → 线程池）
        ok = await asyncio.to_thread(AlertDAO.confirm, alert_id, status_text)
        logger.info(f"告警确认结果：alert_id={alert_id}, ok={ok}")
        return {"code": 200 if ok else 500, "msg": "确认成功" if ok else "确认失败", "data": bool(ok)}
    except Exception as e:
        logger.error(f"告警确认异常：{e}")
        return JSONResponse(status_code=500, content={"code": 500, "msg": "告警确认失败", "data": None})


@alert_router.post("/alert/detect")
async def alert_detect(payload: DetectSchema, current: TokenData = Depends(get_current_user)):
    """
    手动触发三层检测（**无副作用**）

    只调用纯计算层：
        detect_anomaly(user_id, mode="manual") → risk_level_of(...) → rag_search_with_fallback(advice)
    绝不写 alert_record、绝不触发通知（AC-10）。
    """
    user_id = str(payload.user_id)
    logger.info(f"手动检测请求：user_id={user_id}, current={current.user_id}")
    # 数据权限：只能检测自己 / 绑定老人 / 任意用户（管理员）
    if not await _can_access(current, user_id):
        logger.warning(f"手动检测越权被拒绝：current={current.user_id} -> {user_id}")
        return JSONResponse(status_code=403, content={"code": 403, "msg": "无权检测该用户数据", "data": None})

    try:
        # 纯计算层：只读时序数据 + 计算，无任何写库/通知（契约第 7 节）
        from app.core.detect.anomaly_detector import detect_anomaly, risk_level_of

        anomalies = await asyncio.to_thread(detect_anomaly, user_id, "manual")
        anomalies = anomalies or []
        level = await asyncio.to_thread(risk_level_of, anomalies)

        # 处置建议：RAG（向量 → 关键词 → 静态建议），同步函数直接调用即可
        try:
            from app.core.fallback import rag_search_with_fallback

            advice = rag_search_with_fallback("老年人身体指标异常的日常处置建议", 3)
        except Exception as e:
            logger.error(f"检测建议检索失败（降级为静态文案）：{e}")
            advice = "指标异常请先安静休息并复测；若持续异常或出现胸痛、呼吸困难，请立即拨打 120。"

        status_text = "abnormal" if anomalies else "normal"
        logger.info(f"手动检测完成：user_id={user_id}, level={level}, 异常项={len(anomalies)}")
        # 注意：此处不写 alert_record、不发通知（无副作用）
        return {
            "code": 200,
            "msg": "检测完成",
            "data": {
                "status": status_text,
                "level": level,
                "anomalies": anomalies,
                "advice": advice,
            },
        }
    except Exception as e:
        logger.error(f"手动检测异常：{e}")
        return {
            "code": 200,
            "msg": "检测降级",
            "data": {
                "status": "normal",
                "level": "正常",
                "anomalies": [],
                "advice": "暂时无法完成检测，请稍后重试；如身体不适请咨询医生或拨打 120。",
            },
        }


# ---------------- 调用示例 ----------------
# 1) 告警列表（家属不传 user_id 即查全部绑定老人）
#    curl "http://localhost:8000/alert/list?limit=20" -H "Authorization: Bearer <token>"
# 2) 告警确认
#    curl -X POST http://localhost:8000/alert/12/confirm -H "Authorization: Bearer <token>" \
#         -H "Content-Type: application/json" -d '{"status":"已确认"}'
# 3) 手动检测（无副作用，可反复调用，alert_record 行数不变）
#    curl -X POST http://localhost:8000/alert/detect -H "Authorization: Bearer <token>" \
#         -H "Content-Type: application/json" -d '{"user_id":"u001"}'
