"""
语音输出 · EdgeTTS 语音播报智能体（单例）

链路（需求文档 5.3 / 10.6）：
    健康建议文本 → edge_tts.Communicate(text, settings.tts_voice)
    → 合成 mp3 落盘 app/html/tts/ → 返回可访问 URL /static/tts/xxx.mp3
    → 前端 <audio> 自动播放

为什么落盘成静态文件而不是直接返回音频流：
    app/main.py 把 ./app/html 挂载成 /static，所以只要文件写进 app/html/tts/，
    就能被浏览器直接访问、被 <audio> 缓存与复用；同时避免长文本反复合成。

降级设计：
    edge_tts 未安装 / 网络不可用（EdgeTTS 需要访问微软服务）/ 合成失败
    → 记日志并返回空字符串 ""，由路由层决定是否退回"只显示文字"，
    **不抛异常**（NFR-02：语音播报失败不能让问询主流程失败）。

清理策略：
    每次合成前顺手清理超过 TTL 的旧 mp3（简单实现：按修改时间删），
    避免长期运行时 app/html/tts/ 无限膨胀。
"""
import asyncio
import os
import time
import uuid

from app.core.config import settings
from app.core.logger import logger

# 产物目录（相对项目根）：app/html/tts/ → 访问 URL 前缀 /static/tts/
TTS_SUBDIR = os.path.join("app", "html", "tts")
# 静态资源访问前缀（与 main.py 的 app.mount("/static", ...) 对应）
STATIC_PREFIX = "/static/tts"
# 旧文件保留时长（秒）：默认 1 天，够前端重播与调试
FILE_TTL_SECONDS = 24 * 60 * 60


class TTSAgent:
    """EdgeTTS 语音合成（单例）：文本 → mp3 静态文件 → URL"""

    # 单例实例
    _tts = None

    def __init__(self):
        # 静态目录只计算一次（static_dir() 同时负责创建）
        self.dir = self.static_dir()
        # edge_tts 是否可用（首次调用时探测，避免导入期访问网络）
        self.available = None

    # ---------------- 单例 ----------------
    @staticmethod
    def get_tts() -> "TTSAgent":
        """获取全局唯一的 TTSAgent 实例"""
        if TTSAgent._tts is None:
            TTSAgent._tts = TTSAgent()
            logger.info("TTSAgent 单例创建完成")
        return TTSAgent._tts

    # ---------------- 静态目录 ----------------
    def static_dir(self) -> str:
        """返回 TTS 产物目录（app/html/tts/），不存在则自动创建"""
        directory = os.path.abspath(TTS_SUBDIR)
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception as e:
            logger.error(f"TTS 产物目录创建失败：{e}")
        return directory

    # ---------------- 合成 ----------------
    async def speak(self, text: str, file_name: str | None = None) -> str:
        """
        合成语音并返回 URL。

        返回：成功 → "/static/tts/xxxx.mp3"；失败/不可用 → ""（空字符串，绝不抛异常）
        """
        content = "" if text is None else str(text).strip()
        if not content:
            logger.warning("TTS 文本为空，跳过合成")
            return ""

        # 1. 依赖延迟导入：edge_tts 未安装时直接降级
        try:
            import edge_tts
        except ImportError as e:
            self.available = False
            logger.warning(f"未安装 edge_tts，语音播报不可用（改为只展示文字）：{e}")
            return ""
        except Exception as e:
            self.available = False
            logger.error(f"edge_tts 导入失败：{e}")
            return ""

        # 2. 目标文件：外部传入则用它（需自行保证后缀），否则用 uuid 防重名
        name = file_name.strip() if file_name and str(file_name).strip() else f"{uuid.uuid4().hex}.mp3"
        if not name.lower().endswith(".mp3"):
            name = f"{name}.mp3"
        path = os.path.join(self.dir, name)

        # 3. 合成（EdgeTTS 是网络服务，必须在 try 内，断网要能降级）
        try:
            communicate = edge_tts.Communicate(content, settings.tts_voice)
            await communicate.save(path)
            self.available = True
            logger.info(f"语音合成成功：{path}（音色 {settings.tts_voice}）")
        except Exception as e:
            self.available = False
            logger.error(f"语音合成失败（改为只展示文字）：{e}")
            # 失败时清理可能产生的半成品文件，避免前端播放到损坏音频
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            return ""

        # 4. 顺手清理过期产物（简单实现，失败不影响本次返回）
        try:
            self._clean_expired()
        except Exception as e:
            logger.warning(f"TTS 过期文件清理失败（忽略）：{e}")

        # 5. 返回静态 URL（main.py 把 ./app/html 挂载为 /static）
        return f"{STATIC_PREFIX}/{name}"

    # ---------------- 内部：清理过期文件 ----------------
    def _clean_expired(self) -> int:
        """删除超过 TTL 的旧 mp3，返回删除数量"""
        deleted = 0
        now = time.time()
        try:
            for name in os.listdir(self.dir):
                if not name.lower().endswith(".mp3"):
                    continue
                full = os.path.join(self.dir, name)
                try:
                    if now - os.path.getmtime(full) > FILE_TTL_SECONDS:
                        os.remove(full)
                        deleted += 1
                except Exception:
                    continue
        except Exception:
            return deleted
        if deleted:
            logger.info(f"TTS 清理过期语音文件 {deleted} 个")
        return deleted


if __name__ == "__main__":
    import asyncio

    async def test():
        # 自测：edge_tts 不可用或断网时返回 ""，不抛异常
        agent = TTSAgent.get_tts()
        print("TTS 产物目录：", agent.static_dir())
        url = await agent.speak("张奶奶您好，您的血压有点高，请先坐下休息，五分钟后再量一次。", None)
        print("合成结果 URL（失败为空字符串）：", repr(url))

    asyncio.run(test())
