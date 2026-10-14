"""
自动检测任务（契约第 20 节，接口锁定 / FR-06 + FR-07）

两个入口：
    1. `auto_detect_all()`（Celery Beat 定时任务，每 settings.detect_interval_minutes 分钟）：
       全量老人 → 三层检测（纯计算） → 命中写 alert_record → 通知家属；
    2. `auto_detect_once(user_id)`（**双通道并发闭环**，供手动检测接口/演示调用）：
       asyncio.gather 并发两条通道，互不阻塞（return_exceptions=True）：
           通道 1 Celery → 邮件通知家属（短信通道已删除）；
           通道 2 WebSocket → 老人端弹窗 + 语音播报（action="voice_play"）。

分层纪律（需求文档 10.3 / AC-10）：
    `detect_anomaly` 是纯计算层，不写库不发通知；
    **副作用只发生在本模块**（写告警表、触发双通道通知）。
"""
import asyncio

from app.core.celery_app import celery_app
from app.core.detect.anomaly_detector import detect_anomaly, risk_level_of
from app.core.fallback import rag_search_with_fallback
from app.core.logger import logger
from app.core.task.notify_task import notify_family_async

# WebSocket 推送动作标识：前端据此触发语音播报
WS_ACTION_VOICE = "voice_play"


def _build_notify_content(level: str, anomalies: list[dict], advice: str = "") -> str:
    """拼装家属通知正文：级别 + 异常描述（最多 3 条）+ 处置建议"""
    descriptions = [
        str(item.get("description") or "").strip()
        for item in anomalies
        if isinstance(item, dict) and item.get("description")
    ][:3]
    body = "；".join(descriptions) if descriptions else "检测到健康指标异常"
    content = f"【{level}预警】{body}。请及时联系老人确认情况。"
    if advice:
        content += f"\n处置建议：{advice}"
    return content


def _advice_query(anomalies: list[dict]) -> str:
    """由异常项生成检索问题（用前两条描述做语义检索）"""
    descriptions = [
        str(item.get("description") or "").strip()
        for item in anomalies
        if isinstance(item, dict) and item.get("description")
    ][:2]
    return "，".join(descriptions) if descriptions else "老年健康异常处置建议"


def _notify_summary(result) -> str:
    """把通知结果压成一行文本，回写 alert_record.notify_result 留痕"""
    if isinstance(result, dict):
        channel = result.get("channel") or "unknown"
        return f"{channel}: {result.get('msg') or ''}"[:256]
    return str(result)[:256]


@celery_app.task(name="auto_detect_all")
def auto_detect_all() -> dict:
    """
    全量自动检测（Celery Beat 定时任务）。

    返回：{"users": 参与检测的老人数, "alerts": 写入的告警条数}
    """
    logger.info("========== 自动检测轮次开始 ==========")
    user_count = 0
    alert_count = 0
    notified_count = 0

    try:
        from app.dao.alert_dao import AlertDAO
        from app.dao.user_dao import UserDAO

        elders = UserDAO.list_elders() or []
    except Exception as e:
        logger.error(f"自动检测无法获取老人列表，本轮跳过：{type(e).__name__}: {e}")
        return {"users": 0, "alerts": 0}

    if not elders:
        logger.warning("没有 role='elder' 的用户，自动检测空转结束")
        return {"users": 0, "alerts": 0}

    for user_id in elders:
        user_count += 1
        try:
            anomalies = detect_anomaly(user_id, mode="auto")
        except Exception as e:  # detect_anomaly 已自带降级，这里再兜一层，保证单用户失败不影响全量
            logger.error(f"用户 {user_id} 检测失败（跳过）：{type(e).__name__}: {e}")
            continue

        if not anomalies:
            continue

        level = risk_level_of(anomalies)
        content = _build_notify_content(level, anomalies)
        for item in anomalies:
            try:
                # ① 写告警表（副作用层）
                alert_id = AlertDAO.save(
                    user_id,
                    item.get("type") or "",
                    item.get("level") or level,
                    item.get("value"),
                    item.get("description") or "",
                    source="auto",
                )
                if not alert_id:
                    logger.error(f"告警入库失败：user_id={user_id} type={item.get('type')}")
                    continue
                alert_count += 1
                # ② 通知家属（Celery 异步投递）
                result = notify_family_async(
                    user_id, item.get("level") or level, content, "email"
                )
                if isinstance(result, dict) and result.get("ok"):
                    notified_count += 1
                # ③ 通知结果回写告警表（NFR-02 全程留痕）
                try:
                    AlertDAO.update_notify_result(
                        alert_id, _notify_summary(result)
                    )
                except Exception as inner:
                    logger.error(f"回写通知结果失败：{type(inner).__name__}: {inner}")
            except Exception as e:
                logger.error(
                    f"处理告警失败：user_id={user_id} type={item.get('type')} "
                    f"{type(e).__name__}: {e}"
                )

    logger.info(
        f"========== 自动检测轮次结束：用户 {user_count} 人，告警 {alert_count} 条，"
        f"通知成功 {notified_count} 条 =========="
    )
    return {"users": user_count, "alerts": alert_count, "notified": notified_count}


async def auto_detect_once(user_id: str) -> dict:
    """
    单用户"检测 → 写告警 → 双通道通知"完整闭环（双通道并发，互不阻塞）。

    返回：
        {"user_id", "level", "alerts", "alert_ids", "message",
         "celery": {...}, "websocket": {...}}
    """
    result = {
        "user_id": user_id,
        "level": "正常",
        "alerts": 0,
        "alert_ids": [],
        "message": "",
        "celery": None,
        "websocket": None,
    }

    # ---------------- ① 三层检测（纯计算，放线程池避免阻塞事件循环） ----------------
    try:
        anomalies = await asyncio.to_thread(detect_anomaly, user_id, "auto")
    except Exception as e:
        logger.error(f"[auto_detect_once] 检测异常：user_id={user_id} {type(e).__name__}: {e}")
        anomalies = []

    if not anomalies:
        logger.info(f"[auto_detect_once] 用户 {user_id} 未发现异常，流程结束")
        return result

    level = risk_level_of(anomalies)
    result["level"] = level
    result["alerts"] = len(anomalies)
    logger.warning(f"[auto_detect_once] 用户 {user_id} 命中 {len(anomalies)} 条异常，级别 {level}")

    # ---------------- ② 取处置建议（RAG 三级降级，同步函数放线程池） ----------------
    advice = ""
    try:
        advice = await asyncio.to_thread(rag_search_with_fallback, _advice_query(anomalies), 3)
    except Exception as e:
        logger.error(f"[auto_detect_once] 处置建议检索失败：{type(e).__name__}: {e}")

    message = _build_notify_content(level, anomalies, advice)
    result["message"] = message

    # ---------------- ③ 写告警表（副作用层） ----------------
    alert_ids: list[int] = []
    try:
        from app.dao.alert_dao import AlertDAO

        for item in anomalies:
            try:
                alert_id = await asyncio.to_thread(
                    AlertDAO.save,
                    user_id,
                    item.get("type") or "",
                    item.get("level") or level,
                    item.get("value"),
                    item.get("description") or "",
                    "auto",
                )
                if alert_id:
                    alert_ids.append(alert_id)
            except Exception as e:
                logger.error(f"[auto_detect_once] 告警入库失败：{type(e).__name__}: {e}")
    except Exception as e:
        logger.error(f"[auto_detect_once] AlertDAO 不可用：{type(e).__name__}: {e}")
    result["alert_ids"] = alert_ids
    # 推送消息里的 alert_id：取最新写入的一条（无则 None）
    primary_alert_id = alert_ids[-1] if alert_ids else None

    # ---------------- ④ 双通道并发 ----------------
    async def channel_celery() -> dict:
        """通道 1：Celery → 邮件通知家属（短信通道已删除）"""
        return await asyncio.to_thread(notify_family_async, user_id, level, message, "email")

    async def channel_websocket() -> dict:
        """通道 2：WebSocket → 老人端弹窗 + 自动语音播报"""
        from app.web.websocker_router.websocket_notify import ws_manager

        payload = {
            "type": "health_alert",
            "alert_id": primary_alert_id,
            "user_id": user_id,
            "level": level,
            "message": message,
            "action": WS_ACTION_VOICE,
        }
        pushed = await ws_manager.send_to_user(user_id, payload)
        return {"ok": bool(pushed), "channel": "websocket", "msg": "已推送到老人端"}

    channel_results = await asyncio.gather(
        channel_celery(), channel_websocket(), return_exceptions=True
    )
    names = ("celery", "websocket")
    for name, channel_result in zip(names, channel_results):
        if isinstance(channel_result, BaseException):
            logger.error(f"[auto_detect_once] {name} 通道异常：{type(channel_result).__name__}: {channel_result}")
            result[name] = {"ok": False, "channel": name, "msg": str(channel_result)}
        else:
            result[name] = channel_result
    logger.info(f"[auto_detect_once] 用户 {user_id} 双通道结果：{result['celery']} / {result['websocket']}")

    # ---------------- ⑤ 通知结果回写告警表（全程留痕，NFR-02） ----------------
    if primary_alert_id:
        try:
            from app.dao.alert_dao import AlertDAO

            summary = _notify_summary(result.get("celery"))
            await asyncio.to_thread(AlertDAO.update_notify_result, primary_alert_id, summary)
        except Exception as e:
            logger.error(f"[auto_detect_once] 回写通知结果失败：{type(e).__name__}: {e}")
    return result


if __name__ == "__main__":
    # 自测：Celery 与 WebSocket 依赖不一定就绪，本函数必须依然返回 dict（不抛异常）
    # 真实演练示例（需 Redis + WS 服务）：
    #     asyncio.run(auto_detect_once("u001"))
    logger.info(f"Celery 定时任务：{getattr(auto_detect_all, 'name', 'auto_detect_all')}")
    logger.info(f"单用户双通道闭环结果：{asyncio.run(auto_detect_once('u001'))}")
