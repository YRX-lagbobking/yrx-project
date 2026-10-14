"""
WebSocket 实时通知路由（双通道告警的"通道 2"）

职责：
    1. 维护 user_id → 多端 WebSocket 连接（老人可能同时在手机与网页端在线）；
    2. 供后台检测任务 / 节点通过 ws_manager.send_to_user() 主动推送 health_alert；
    3. 接收老人端的"我已知道"回执（ack:<alert_id>），可选地把告警标记为已确认。

设计说明：
    1. 一个用户允许多端在线，因此用 dict[user_id, list[WebSocket]]；
    2. send_to_user 必须"永不抛异常"：用户不在线返回 False，发送失败清理该连接（契约第 0 节返回风格）；
    3. 推送内容由调用方（节点 / detect_task）组织，本模块不做业务判断。
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logger import logger

ws_router = APIRouter()


class ConnectionManager:
    """WebSocket 连接管理器（进程内单例，多进程部署需换成 Redis Pub/Sub）"""

    def __init__(self):
        # user_id → 该用户的所有在线连接（多端在线）
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        """接受连接并注册到用户名下"""
        # 先握手（accept 内部容错，重复 accept 只记日志）
        try:
            await websocket.accept()
        except Exception as e:
            logger.warning(f"WebSocket accept 异常（可能已握手）：{e}")
        user_id = str(user_id)
        self.active_connections.setdefault(user_id, []).append(websocket)
        logger.info(f"WebSocket 连接注册：user_id={user_id}，当前该用户在线数={len(self.active_connections[user_id])}")

    def disconnect(self, user_id: str, websocket: WebSocket | None = None) -> None:
        """
        断开连接

        websocket 为空时清理该用户的全部连接；否则只移除指定连接。
        """
        user_id = str(user_id)
        conns = self.active_connections.get(user_id)
        if conns is None:
            logger.info(f"WebSocket 断开：user_id={user_id} 无注册连接（忽略）")
            return
        if websocket is None:
            # 指定用户全部下线
            self.active_connections.pop(user_id, None)
            logger.info(f"WebSocket 断开：user_id={user_id} 全部连接已清理")
            return
        # 只移除指定连接（列表里可能还有该用户的其它端）
        try:
            conns.remove(websocket)
        except ValueError:
            pass
        if not conns:
            self.active_connections.pop(user_id, None)
        logger.info(f"WebSocket 断开：user_id={user_id}，剩余在线数={len(self.active_connections.get(user_id, []))}")

    async def send_to_user(self, user_id: str, data: dict) -> bool:
        """
        向指定用户的所有在线连接推送 JSON 数据

        返回 True 表示至少有一端送达；用户不在线或全部失败返回 False。
        本方法不得抛异常给调用方（后台检测任务不能被推送失败拖垮）。
        """
        try:
            user_id = str(user_id)
            conns = self.active_connections.get(user_id)
            if not conns:
                logger.info(f"用户 {user_id} 当前不在线，推送跳过")
                return False
            ok = False
            # 复制一份再遍历：发送失败时会在循环内修改原列表
            for ws in list(conns):
                try:
                    await ws.send_json(data)
                    ok = True
                except Exception as e:
                    logger.warning(f"推送失败，清理该连接：user_id={user_id}, err={e}")
                    self.disconnect(user_id, ws)
            return ok
        except Exception as e:
            logger.error(f"用户 {user_id} 推送异常：{e}")
            return False

    def is_online(self, user_id: str) -> bool:
        """判断用户是否有在线连接"""
        return bool(self.active_connections.get(str(user_id)))


# 模块级单例：main.py 的 lifespan 中同时挂到 app.state.ws_manager
ws_manager = ConnectionManager()


@ws_router.websocket("/ws/notify")
async def ws_notify(websocket: WebSocket):
    """
    告警实时推送 WebSocket

    用法：前端 `new WebSocket("ws://host/ws/notify?user_id=u001")`
    服务端推送：{"type":"health_alert","alert_id":1,"level":"高危","message":"...","action":"voice_play"}
    客户端回执：`ack:<alert_id>`（表示"我已知道"）
    """
    # 从 query 参数取 user_id 完成注册
    user_id = websocket.query_params.get("user_id")
    if not user_id:
        # 没有 user_id 无法投递，直接关闭（1008 = policy violation）
        logger.warning("WebSocket 连接缺少 user_id 参数，拒绝连接")
        await websocket.close(code=1008)
        return

    # 图智能体的取用约定：WebSocket 场景用 websocket.app.state.health_agent
    health_agent = getattr(websocket.app.state, "health_agent", None)

    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            # 主循环只处理心跳与回执
            data = await websocket.receive_text()
            if data == "ping" or data == "heartbeat":
                # 心跳：回 pong 保活
                await websocket.send_text("pong")
                continue
            if data.startswith("ack:"):
                # 老人已读回执：记录日志，并可选地把告警置为"已确认"
                alert_id = data[4:].strip()
                logger.info(f"收到告警回执：user_id={user_id}, alert_id={alert_id}")
                try:
                    from app.dao.alert_dao import AlertDAO

                    # DAO 是同步函数，连接内直调（教学规模）；生产应改异步连接池/线程池
                    AlertDAO.confirm(int(alert_id), "已确认")
                except Exception as e:
                    logger.error(f"告警回执落库失败（不影响连接）：{e}")
                continue
            logger.debug(f"WebSocket 收到消息：user_id={user_id}, data={data}")
    except WebSocketDisconnect:
        logger.info(f"WebSocket 客户端主动断开：user_id={user_id}")
    except Exception as e:
        logger.error(f"WebSocket 连接异常：user_id={user_id}, err={e}")
    finally:
        # 无论何种断开方式都要清理注册，避免"幽灵在线"导致推送空转
        ws_manager.disconnect(user_id, websocket)
        # health_agent 在此仅用于确认状态链路可用（推送由后台任务调用 ws_manager）
        if health_agent is None:
            logger.warning(f"WebSocket 断开时 health_agent 不可用：user_id={user_id}")


# ---------------- 调用示例 ----------------
# 1) 前端订阅告警
#    const ws = new WebSocket("ws://localhost:8000/ws/notify?user_id=u001");
#    ws.onmessage = (e) => console.log(JSON.parse(e.data));   // {"type":"health_alert",...}
#    ws.send("ack:12");                                       // 我已知道
# 2) 服务端推送（节点 / detect_task 内）
#    from app.web.websocker_router.websocket_notify import ws_manager
#    await ws_manager.send_to_user("u001", {"type": "health_alert", "alert_id": 12, "level": "高危",
#                                           "message": "心率 140 次/分", "action": "voice_play"})
