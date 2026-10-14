"""
语音路由（Vosk 输入 + EdgeTTS 输出）

职责：
    1. WS   /voice/ws    ：实时语音识别长连接（后台任务持续识别 + 主协程随时停止）；
    2. POST /voice/chat  ：音频文件一次性识别 → 送进同一张 LangGraph 图 → 返回文本问答；
    3. POST /voice/tts   ：文本转 mp3，返回静态 URL，前端自动播报。

设计说明：
    1. 识别放 asyncio.create_task 后台任务，主协程继续 receive_text() 响应停止指令，
       这样"连续多句识别 + 随时可控"两个需求同时满足（AC-01 / AC-02）；
    2. vosk / edge_tts 均为可选依赖，一律函数内延迟导入，未安装时接口降级返回提示而不是崩栈；
    3. 上传的音频用后即删，不留存原始音频（隐私要求 NFR-03）。
"""
import asyncio
import json
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.logger import logger
from app.core.security import TokenData, get_current_user

voice_router = APIRouter()

# 停止指令（前端既可能发纯文本，也可能发 {"action":"stop"}，这里统一兼容）
_STOP_WORDS = {"stop", "停止", "结束", "关闭"}


class TTSSchema(BaseModel):
    """TTS 入参：待播报文本"""

    text: str = Field(..., description="需要转成语音的文本")


def _is_stop_command(data: str) -> bool:
    """判断客户端消息是否为停止指令（兼容纯文本与 JSON 两种写法）"""
    if not data:
        return False
    text = data.strip()
    if text.lower() in _STOP_WORDS:
        return True
    # 兼容 {"action":"stop"} 形式
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            action = str(obj.get("action") or obj.get("type") or "").strip().lower()
            return action in _STOP_WORDS
    except Exception:
        pass
    return False


def _recognize_file(audio_path: str) -> str:
    """
    用 Vosk 对**音频文件**做一次性识别（同步阻塞函数，调用方需用 to_thread 包装）

    说明：流式版本走 VoskAgent.speak()，这里为避免与流式状态冲突，
    直接用 Model + KaldiRecognizer 逐块读取文件识别，互不干扰。
    """
    import wave

    import vosk  # 延迟导入：未安装时抛 ImportError，由调用方降级

    from app.core.config import settings

    if not settings.vosk_path:
        raise RuntimeError("未配置 VOSK_PATH 语音模型路径")
    model = vosk.Model(settings.vosk_path)

    with wave.open(audio_path, "rb") as wf:
        # 采样率以文件自身为准，Vosk 需要显式告知
        rec = vosk.KaldiRecognizer(model, wf.getframerate())
        chunks = []
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            if rec.AcceptWaveform(data):
                # 一句话结束，取最终结果
                chunks.append(json.loads(rec.Result()).get("text", ""))
        # 收尾：把最后一段未结束的音频也识别出来
        chunks.append(json.loads(rec.FinalResult()).get("text", ""))

    return "".join(chunk for chunk in chunks if chunk).strip()


@voice_router.websocket("/voice/ws")
async def voice_ws(websocket: WebSocket):
    """
    实时语音识别 WebSocket

    结构：
        主协程：await websocket.receive_text() → 收到 stop/停止 → 停识别 + 取消后台任务
        后台任务：vosk.speak(websocket) 持续识别并推送 {"type":"asr","text":...}
    """
    logger.debug("语音 WebSocket 握手（/voice/ws）")
    vosk = None
    task = None
    try:
        await websocket.accept()
        # 图智能体的取用约定：WebSocket 场景用 websocket.app.state.health_agent
        health_agent = getattr(websocket.app.state, "health_agent", None)
        if health_agent is None:
            logger.warning("语音连接建立时 health_agent 尚未就绪（仅识别，不影响语音链路）")

        # 从 app/core/config.py 的 VoskAgent 获取单例（延迟导入，避免模型加载阻塞模块导入）
        from app.ai.agent.vosk_agent import VoskAgent

        vosk = VoskAgent.get_vosk()
        # 识别放到后台任务：主协程必须保持空闲以响应"停止"指令
        task = asyncio.create_task(vosk.speak(websocket))
        logger.info("语音识别后台任务已启动")

        while True:
            # 主协程只负责收指令（心跳 / 停止 / 回执）
            data = await websocket.receive_text()
            if _is_stop_command(data):
                logger.info(f"收到停止指令：{data}")
                await vosk.stop()  # 只负责停止监听
                task.cancel()  # 取消后台识别任务
                break
            # 其它消息仅记录，避免打断识别
            logger.debug(f"语音连接收到消息：{data}")
    except WebSocketDisconnect:
        logger.info("语音连接已断开")
    except Exception as e:
        # 异常只记日志，不向上抛（连接类接口向上抛会让 uvicorn 打整栈错误）
        logger.error(f"语音连接异常：{e}")
    finally:
        # ⚠️ 收尾顺序很关键（"语音只能识别一次、之后一直不行"的根因就在原实现里）：
        #   1) 先停监听；
        #   2) 取消识别任务并 await 它真正结束（确保阻塞在 stream.read() 的读线程退出）；
        #   3) 再取尾句（flush）：把最后一段没成句的音频也识别出来推给前端；
        #   4) 最后才 reset() 释放音频设备（此时读线程已退出，操作是安全的）。
        #   原实现是"先 vosk.reset() 释放音频、后 task.cancel()"——读线程还在跑，
        #   却去操作已经 stop_stream/close/terminate 的对象，导致 PyAudio 句柄错乱，
        #   之后再也打不开麦克风，只能重启服务。
        if vosk is not None:
            try:
                await vosk.stop()
            except Exception as e:
                logger.error(f"停止语音识别失败：{e}")
        if task is not None:
            task.cancel()
            try:
                await task
            except BaseException:
                # 任务被取消 / 内部异常都在此吞掉，保证连接收尾流程不中断
                pass
        if vosk is not None:
            try:
                await vosk.flush(websocket)   # 尾句兜底：最后一段未成句的音频也识别出来
            except Exception as e:
                logger.error(f"尾句识别失败：{e}")
            try:
                vosk.reset()                  # 读线程已退出，这里释放音频设备是安全的
            except Exception as e:
                logger.error(f"语音资源释放异常：{e}")
        logger.info("语音连接收尾完成")


@voice_router.post("/voice/chat")
async def voice_chat(
    request: Request,
    file: UploadFile = File(..., description="音频文件（wav）"),
    user_id: Optional[str] = Query(default=None, description="用户 ID，缺省用当前登录用户"),
    session_id: Optional[str] = Query(default=None, description="会话 ID，缺省自动新建"),
    role: str = Query(default="elder", description="调用角色"),
    user: TokenData = Depends(get_current_user),
):
    """
    音频文件一次性问询

    流程：上传 → 临时文件 → Vosk 识别 → 同一张图 chat() → 收集流式片段拼接回复
    返回：{"text": 识别文本, "reply": 回复文本}
    """
    # 未带 user_id 时按当前登录用户处理
    uid = user_id or user.user_id
    real_role = user.role or role or "elder"
    tmp_path = None
    try:
        # 1. 音频落临时文件（不留存原始音频，识别完即删）
        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="voice_")
        content = await file.read()
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        logger.info(f"收到语音文件 {file.filename}，大小={len(content)} 字节，user_id={uid}")

        # 2. Vosk 识别（同步阻塞库，放线程池，避免阻塞事件循环）
        try:
            text = await asyncio.to_thread(_recognize_file, tmp_path)
        except ImportError:
            logger.error("vosk 未安装，语音识别不可用")
            return {"code": 500, "msg": "语音识别不可用"}
        except Exception as e:
            logger.error(f"语音识别失败：{e}")
            return {"code": 500, "msg": "语音识别不可用"}

        if not text:
            logger.warning("语音识别结果为空")
            return {"text": "", "reply": "没有听清您说的话，请再说一遍好吗？"}

        # 3. 识别文本送进同一张 LangGraph 图
        health_agent = request.app.state.health_agent
        sid = session_id
        if not sid:
            # 会话缺失时自动新建，保证图和记忆层有 thread_id
            sid = await health_agent.create_session(uid)
        reply = ""
        try:
            async for chunk in health_agent.chat(text, uid, sid, real_role):
                # 收集流式片段并拼接成完整回复
                reply += chunk or ""
        except Exception as e:
            logger.error(f"语音问询图执行异常：{e}")
            reply = "抱歉，系统暂时无法回答，请稍后再试。"

        logger.info(f"语音问询完成：session_id={sid}, text={text}, reply_len={len(reply)}")
        return {"text": text, "reply": reply}
    except Exception as e:
        logger.error(f"语音文件问询异常：{e}")
        return {"code": 500, "msg": "语音识别不可用"}
    finally:
        # 4. 删除临时音频
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                logger.error(f"临时音频删除失败：{e}")


@voice_router.post("/voice/tts")
async def voice_tts(payload: TTSSchema, user: TokenData = Depends(get_current_user)):
    """文本转语音：返回 /static/tts/xxx.mp3，失败返回空字符串 + 提示"""
    try:
        # edge_tts 为可选依赖，TTSAgent 内部延迟导入并降级
        from app.ai.agent.tts_agent import TTSAgent

        audio_url = await TTSAgent.get_tts().speak(payload.text)
        if not audio_url:
            logger.warning("TTS 返回空地址（edge_tts 不可用或合成失败）")
            return {"audio": "", "msg": "语音合成不可用"}
        logger.info(f"TTS 合成成功：{audio_url}")
        return {"audio": audio_url}
    except ImportError:
        logger.error("edge_tts / TTSAgent 不可用")
        return {"audio": "", "msg": "语音合成不可用"}
    except Exception as e:
        logger.error(f"TTS 合成异常：{e}")
        return {"audio": "", "msg": "语音合成不可用"}


# ---------------- 调用示例 ----------------
# 1) 语音识别 WebSocket（浏览器/客户端持续推送音频）
#    js: const ws = new WebSocket("ws://localhost:8000/voice/ws"); ws.send("stop")  // 停止识别
# 2) 音频文件一次性问询
#    curl -X POST "http://localhost:8000/voice/chat?user_id=u001" -H "Authorization: Bearer <token>" \
#         -F "file=@./record.wav"
# 3) 文本转语音
#    curl -X POST http://localhost:8000/voice/tts -H "Authorization: Bearer <token>" \
#         -H "Content-Type: application/json" -d '{"text":"您的血压偏高，请注意休息"}'
