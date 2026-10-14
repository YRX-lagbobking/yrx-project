"""
语音输入 · Vosk 离线语音识别智能体（单例）

链路（需求文档 10.6）：
    麦克风 → PyAudio 16kHz 单声道 → KaldiRecognizer(model, 16000)
    → AcceptWaveform(4096 帧) 返回 True（一句话结束）→ json.loads(re.Result())["text"]
    → WebSocket 推给前端

帧长为什么取 4096：
    4096 / 16000 ≈ 0.256s，是"识别延迟"与"CPU/内存开销"的平衡点：
    帧太小（如 800≈0.05s）会让 AcceptWaveform 调用过于频繁、CPU 占用升高；
    帧太大（如 16384≈1s）会让"一句话结束"的判定明显滞后，前端打字机效果变差。

【推送协议】统一 JSON（文本内容在 text 字段），前端据此决定"能不能当用户输入"：
    {"type": "ready",   "text": "开始说话"}   ← 麦克风就绪，提示用户可以开口
    {"type": "partial", "text": "我今天"}      ← 实时中间结果（话还没说完），只展示不发送
    {"type": "asr",     "text": "我今天血压高"} ← 一句话识别完成，前端据此自动发送
    {"type": "tip",     "text": "..."}         ← 提示类（不可用等），**不能**当用户输入
    为什么必须区分类型：原来只推纯文本，前端"收到什么就自动发送"，
    导致"语音识别不可用，请改用文字输入"这句提示被当成用户消息发出去。

必须修复的历史缺陷（AC-01 / AC-02）：
    exam_0914 的实现把"停止监听"写进了 send_msg() —— 识别到第一句话就把 listing 置为 False，
    结果只能识别一句就退出循环，且"停止"和"发送"两个职责混在一起无法单独控制。
    本实现的职责拆分：
        speak(ws)        只负责"持续识别循环"（内部调用 send_msg 推送结果，推完继续循环）
        send_msg(...)    **只负责发送**，绝不修改 self.listing
        stop()           **只负责停止**（置 listing=False，循环下一轮自然退出）
        flush(ws)        **只负责收尾**：把最后一段未结束的音频识别出来推给前端
        reset()          重置状态，供下一次 WebSocket 连接复用（必须在识别任务结束后调用）
    这样"连续多句识别 + 随时可停"同时成立：识别放 asyncio.create_task 后台任务，
    主协程继续 receive_text() 响应前端的停止指令。

可用性约定（服务必须能起来）：
    vosk / pyaudio 未安装、VOSK_PATH 未配置、模型文件缺失、麦克风不可用
    → 记日志 + 推送 {"type":"tip"}（前端只提示、不发送）+ 正常返回，**不抛异常**。
"""
import asyncio
import json

from app.core.config import settings
from app.core.logger import logger

# 采样率固定 16kHz（Vosk 中文模型要求）
SAMPLE_RATE = 16000
# 每帧 4096 帧 ≈ 0.256 秒，延迟与性能的平衡点（详见模块说明）
FRAME_SIZE = 4096
# 语音不可用时的前端提示文案（以 tip 类型推送，前端不会把它当成用户输入）
UNAVAILABLE_TIP = "语音识别不可用，请改用文字输入"
# 麦克风就绪后的握手指令（前端据此提示"可以说话了"，避免用户提前说话被丢字）
READY_TIP = "开始说话"


class VoskAgent:
    """Vosk 语音识别（单例）：持续识别循环 + 单一职责的发送/停止/收尾"""

    # 单例实例
    _vosk = None

    def __init__(self):
        # 是否处于监听状态（True 时 speak 的 while 循环持续运行）
        self.listing = False
        # 该标志由 speak() 设置：识别循环因异常退出后，speak 的 finally 会把 listing 复位
        self.available = True
        # 模型与识别器（首次调用 speak 时懒加载，构造期不做重型初始化）
        self.model = None
        self.re = None
        self.stream = None
        self.audio = None
        # 懒加载失败的原因（日志与排查用）
        self.init_error = ""

    # ---------------- 单例 ----------------
    @staticmethod
    def get_vosk() -> "VoskAgent":
        """获取全局唯一的 VoskAgent 实例"""
        if VoskAgent._vosk is None:
            VoskAgent._vosk = VoskAgent()
            logger.info("VoskAgent 单例创建完成")
        return VoskAgent._vosk

    # ---------------- 初始化（懒加载 + 全量容错） ----------------
    def _ensure_ready(self) -> bool:
        """
        确保模型 / 识别器 / 音频流就绪。

        返回 True 表示可以开始识别；任何一步失败都返回 False 并记录原因，
        由 speak() 统一推送 {"type":"tip"} 提示（不抛异常）。
        """
        if self.re is not None and self.stream is not None:
            return True
        try:
            # 1. 依赖延迟导入：未安装时只影响语音能力，不影响服务启动
            import pyaudio
            from vosk import KaldiRecognizer
        except ImportError as e:
            self.init_error = f"未安装语音依赖（vosk / pyaudio）：{e}"
            logger.warning(self.init_error)
            return False
        except Exception as e:
            self.init_error = f"语音依赖导入失败：{e}"
            logger.error(self.init_error)
            return False

        try:
            # 2. 加载 Vosk 模型（模型路径来自配置，未配置时直接判不可用）
            self.model = self._load_model() if settings.vosk_path else None
            if self.model is None:
                self.init_error = "Vosk 模型不可用（请检查 VOSK_PATH 与模型文件）"
                logger.warning(self.init_error)
                return False

            # 3. 建立识别器：中文模型 + 16kHz
            self.re = KaldiRecognizer(self.model, SAMPLE_RATE)

            # 4. 打开音频输入流：16 位、单声道、16kHz、每帧 4096
            #    MIC_DEVICE_INDEX 可选：留空用系统默认设备；填了就用指定设备（多设备/默认设备
            #    不支持 16kHz 时很有用）。默认设备多为 44.1k/48k 立体声，Windows 会做转换，
            #    但指定一个原生支持 16k 的设备更稳。
            self.audio = pyaudio.PyAudio()
            open_kwargs = {}
            device_index = str(getattr(settings, "mic_device_index", "") or "").strip()
            if device_index:
                idx = int(device_index)
                open_kwargs["input_device_index"] = idx
                try:
                    if not self.audio.is_format_supported(
                        SAMPLE_RATE, input_device_index=idx, input_channels=1
                    ):
                        logger.warning(
                            f"麦克风设备 {idx} 未声明支持 16kHz 单声道，"
                            f"识别可能不稳定；建议改 MIC_DEVICE_INDEX 或 .env 换设备"
                        )
                except Exception as e:
                    logger.warning(f"麦克风格式检查失败（忽略）：{e}")
            self.stream = self.audio.open(
                format=pyaudio.paInt16,       # 16 位整数格式
                channels=1,                   # 单声道
                rate=SAMPLE_RATE,             # 16kHz 采样率
                input=True,                   # 输入模式（录音）
                frames_per_buffer=FRAME_SIZE, # 每个缓冲区 4096 帧
                **open_kwargs,
            )
            return True
        except Exception as e:
            self.init_error = f"语音设备/识别器初始化失败：{e}"
            logger.error(self.init_error)
            return False

    @staticmethod
    def _load_model():
        """加载 Vosk 模型；未安装 vosk / 路径为空 / 模型损坏时返回 None"""
        try:
            from vosk import Model  # 延迟导入

            if not settings.vosk_path:
                logger.warning("VOSK_PATH 未配置，语音识别不可用")
                return None
            return Model(model_path=settings.vosk_path)
        except Exception as e:
            logger.error(f"Vosk 模型加载失败：{e}")
            return None

    # ---------------- 持续识别循环 ----------------
    async def speak(self, ws) -> None:
        """
        持续识别循环（后台任务运行）。

        要点：
            1. 就绪后先推 {"type":"ready"}：明确告诉用户"可以说话了"，避免抢跑丢字；
            2. 每次 AcceptWaveform 返回 True 表示"一句结束"，取出 text 推送后**继续循环**；
            3. 未成句时推 {"type":"partial"} 实时中间结果（文本变化才推，避免刷屏），
               让用户看得见"正在识别"，而不是对着麦克风干等；
            4. stream.read 是阻塞调用，丢进 run_in_executor，避免卡死事件循环
               （否则同一进程里的 SSE / WebSocket 推送全部停摆）；
            5. 每轮 await asyncio.sleep(0) 主动让出事件循环，保证 stop() 能被及时处理；
            6. 任何异常都只记日志并正常返回，绝不冒泡到 WebSocket 处理函数。
        """
        # 依赖/设备不可用：推送 tip（前端只提示、不发送），然后正常返回
        if not self._ensure_ready():
            try:
                await self.send_msg(UNAVAILABLE_TIP, ws, kind="tip")
            except Exception as e:
                logger.error(f"语音不可用提示发送失败：{e}")
            return

        # 进入监听状态
        self.listing = True
        logger.info("语音识别开始（支持连续多句，stop() 可随时停止）")
        # 握手：告诉前端"麦克风已就绪，可以说话了"
        try:
            await self.send_msg(READY_TIP, ws, kind="ready")
        except Exception as e:
            logger.warning(f"就绪提示发送失败：{e}")

        partial_prev = ""
        try:
            while self.listing:
                # 阻塞式读音频：放到线程池执行，避免阻塞事件循环
                loop = asyncio.get_running_loop()
                data = await loop.run_in_executor(None, self._read_frame)
                # 主动让出事件循环，保证 stop()/其它协程有机会执行
                await asyncio.sleep(0)
                if not data:
                    # 流已被 reset() 释放：退出循环（避免空转）
                    if self.stream is None:
                        logger.info("音频流已释放，识别循环退出")
                        break
                    continue
                # AcceptWaveform 返回 True 表示一句话结束
                if self.re.AcceptWaveform(data):
                    text = self._result_text(self.re.Result())
                    partial_prev = ""
                    if text:
                        # 只负责发送；发送后继续循环 → 连续多句识别
                        await self.send_msg(text, ws, kind="asr")
                else:
                    # 实时中间结果：话还没说完时给前端一个"正在识别"的反馈
                    partial = self._partial_text(self.re.PartialResult())
                    if partial and partial != partial_prev:
                        partial_prev = partial
                        await self.send_msg(partial, ws, kind="partial")
        except asyncio.CancelledError:
            # 被取消（前端点停止 / 连接断开）：正常收尾，交给 flush() 取尾句
            logger.info("语音识别任务被取消")
            raise
        except Exception as e:
            # 麦克风断开 / WebSocket 已关闭等：只记日志
            logger.error(f"语音识别循环异常退出：{e}")
        finally:
            # 循环退出（stop() 或异常）统一复位监听标志；音频资源由外层的 flush()+reset() 处理
            self.listing = False
            logger.info("语音识别循环已结束")

    # ---------------- 只负责发送 ----------------
    async def send_msg(self, text: str, ws, kind: str = "asr") -> None:
        """
        只负责把消息推给前端（统一 JSON 协议，见模块说明）。

        【硬性约束】本方法**不得**修改 self.listing —— 历史上就是在这里写了
        self.listing = False，导致"识别到一句就退出循环"（AC-01 不通过）。
        停止只由 stop() 负责。
        """
        if ws is None:
            logger.warning(f"WebSocket 为空，无法发送语音消息：{text}")
            return
        try:
            payload = {"type": kind, "text": text}
            if hasattr(ws, "send_json"):
                await ws.send_json(payload)
            elif hasattr(ws, "send_text"):
                await ws.send_text(json.dumps(payload, ensure_ascii=False))
            else:
                logger.warning("WebSocket 无可用发送方法，语音消息被丢弃")
        except Exception as e:
            # 发送失败（连接已关闭）不影响循环继续，只记日志
            logger.error(f"语音消息发送失败：{e}")

    # ---------------- 只负责停止 ----------------
    async def stop(self) -> None:
        """
        只负责停止监听：置 listing=False，speak 的 while 循环下一轮自然退出。

        注意：不在这里关闭音频流（关闭动作统一放在 reset()），
        避免"用户暂停后还想继续说话"时反复申请麦克风设备。
        """
        if self.listing:
            logger.info("收到停止指令，正在停止语音识别")
        self.listing = False

    # ---------------- 收尾：取最后一段未结束的音频 ----------------
    async def flush(self, ws) -> None:
        """
        把最后一段还没成句的音频识别出来并推送（在识别任务结束后调用）。

        为什么需要：用户说完"我头疼"但没停顿就点了停止，AcceptWaveform 不会返回 True，
        这段内容只在 FinalResult() 里；没有这一步就会丢尾句。
        """
        try:
            if self.re is None:
                return
            text = self._result_text(self.re.FinalResult())
            if text:
                logger.info(f"尾句识别结果：{text}")
                await self.send_msg(text, ws, kind="asr")
        except Exception as e:
            logger.warning(f"尾句识别失败（忽略）：{e}")

    # ---------------- 重置状态 ----------------
    def reset(self) -> None:
        """
        重置识别状态并释放音频资源，供下一次 WebSocket 连接使用。

        ⚠️ 调用顺序要求：必须在识别任务**真正结束之后**再调用（见 voice_router 的收尾顺序）。
           否则仍在 stream.read() 阻塞的读线程会操作已销毁的 PyAudio 对象，
           导致设备句柄/COM 状态错乱，之后再也打不开麦克风
           —— 这正是"语音只能识别一次、之后一直不行"的根因。
        这里先把引用置空再释放，让 _read_frame 立刻看到 self.stream is None 并安全退出。
        """
        self.listing = False
        stream, self.stream = self.stream, None
        audio, self.audio = self.audio, None
        self.re = None
        # 释放音频流与 PyAudio 实例（失败只记日志：设备可能已被系统回收）
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        except Exception as e:
            logger.warning(f"音频流释放异常：{e}")
        try:
            if audio is not None:
                audio.terminate()
        except Exception as e:
            logger.warning(f"PyAudio 释放异常：{e}")
        logger.info("VoskAgent 状态已重置")

    # ---------------- 内部工具 ----------------
    def _read_frame(self):
        """
        读一帧音频（阻塞调用，由 run_in_executor 丢进线程池执行）。

        兼容性说明：
            1. reset() 可能已把 self.stream 置空 → 直接返回 b""，让循环安全退出；
            2. pyaudio 老版本没有 exception_on_overflow 参数，缓冲区溢出时应当
               "丢帧继续"而不是抛异常中断整段识别，故这里做一次退让。
        """
        stream = self.stream
        if stream is None:
            return b""
        try:
            return stream.read(FRAME_SIZE, exception_on_overflow=False)
        except TypeError:
            return stream.read(FRAME_SIZE)

    @staticmethod
    def _result_text(result_json: str) -> str:
        """解析 Vosk 返回的 JSON，取出 text 字段（解析失败返回空字符串）"""
        try:
            return (json.loads(result_json) or {}).get("text", "")
        except Exception:
            return ""

    @staticmethod
    def _partial_text(result_json: str) -> str:
        """解析 Vosk 的 PartialResult JSON，取出 partial 字段（解析失败返回空字符串）"""
        try:
            return (json.loads(result_json) or {}).get("partial", "")
        except Exception:
            return ""


if __name__ == "__main__":
    import asyncio

    class _FakeWS:
        """自测用的假 WebSocket：打印"消息类型 + 文本"（验证 JSON 协议）"""

        async def send_json(self, payload):
            print(f"推送前端：[{payload.get('type')}] {payload.get('text')}")

    async def test():
        # 自测 1：本机缺依赖/设备时，应推送 {"type":"tip"} 并正常返回，不抛异常
        agent = VoskAgent.get_vosk()
        await agent.speak(_FakeWS())
        # 自测 2：职责拆分 —— send_msg 不改变监听状态
        agent.listing = True
        await agent.send_msg("测试一句话", _FakeWS())
        print("send_msg 后 listing（应仍为 True）：", agent.listing)
        await agent.stop()
        print("stop() 后 listing（应为 False）：", agent.listing)
        # 自测 3：协议解析
        print("Result 解析：", agent._result_text('{"text": "今天血压有点高"}'))
        print("Partial 解析：", agent._partial_text('{"partial": "今天血压"}'))
        agent.reset()

    asyncio.run(test())
