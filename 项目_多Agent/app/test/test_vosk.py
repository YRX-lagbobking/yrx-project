"""
Vosk 离线语音识别自测：麦克风连续多句识别（FR-01 / AC-01、AC-02）

推荐运行方式（在项目根目录下执行）：
    python -m app.test.test_vosk
    python app/test/test_vosk.py

演示内容：
    1. 用 VOSK_PATH 加载中文 Vosk 模型（延迟导入，缺依赖只降级不报错）；
    2. PyAudio 打开 16kHz / 单声道 / 4096 帧的麦克风流；
    3. KaldiRecognizer.AcceptWaveform 流式识别，**持续多句**：
       识别到一句就打印一句，然后继续听，绝不因第一句结束而退出（AC-01 的历史缺陷）；
    4. 按 Ctrl+C（或说"退出"）结束，释放麦克风流（AC-02 的等价行为）。

依赖：vosk、pyaudio（可选依赖，缺失时打印安装提示并正常退出）。
注意：Windows 下运行 uvicorn 服务需要 asyncio.WindowsSelectorEventLoopPolicy，
      本脚本是纯同步脚本，不受影响。
"""
import json
import os
import sys

# ---------- 项目根目录加入 sys.path ----------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> None:
    print("=" * 72)
    print("Vosk 离线语音识别自测：麦克风连续多句识别（FR-01 / AC-01、AC-02）")
    print("=" * 72)

    # ---------------- 1. 依赖检查（延迟导入，缺了就打印提示退出） ----------------
    try:
        from vosk import KaldiRecognizer, Model
    except ImportError:
        print("[跳过] 未安装 vosk。安装命令：pip install vosk")
        return

    try:
        import pyaudio
    except ImportError:
        print("[跳过] 未安装 pyaudio。Windows 安装命令：pip install pyaudio")
        print("       若安装失败，可先装 pipwin 再执行：pipwin install pyaudio")
        return

    # ---------------- 2. 读取模型路径（统一走 settings，不散落 os.getenv） ----------------
    try:
        from app.core.config import settings

        model_path = settings.vosk_path or os.getenv("VOSK_PATH", "")
    except Exception:
        model_path = os.getenv("VOSK_PATH", "")

    print(f"\n【1】模型路径 VOSK_PATH = {model_path or '(未配置)'}")
    if not model_path:
        print("[跳过] 未配置 VOSK_PATH，请在 .env 中指向中文模型目录，例如：")
        print("       VOSK_PATH=E:\\vosk-model-cn-0.22\\vosk-model-cn-0.22")
        print("       模型下载：https://alphacephei.com/vosk/models")
        return
    if not os.path.isdir(model_path):
        print(f"[跳过] 模型目录不存在：{model_path}")
        return

    print("       正在加载模型（首次加载约需数秒）…")
    try:
        model = Model(model_path=model_path)
    except Exception as e:
        print(f"[跳过] 模型加载失败：{e}")
        return
    print("       模型加载完成")

    # ---------------- 3. 打开麦克风 ----------------
    print("\n【2】打开麦克风：16kHz / 单声道 / 4096 帧")
    p = pyaudio.PyAudio()
    stream = None
    try:
        stream = p.open(
            format=pyaudio.paInt16,   # 16 位整型
            channels=1,               # 单声道
            rate=16000,               # 16kHz 采样率（Vosk 中文模型要求）
            input=True,               # 录音输入
            frames_per_buffer=4096,   # 4096/16000 ≈ 0.256s，延迟与性能的平衡点
        )
    except Exception as e:
        print(f"[跳过] 麦克风打开失败（无设备或被占用）：{e}")
        p.terminate()
        return
    print("       麦克风已打开")

    # ---------------- 4. 连续多句识别（关键：绝不因一句结束而退出） ----------------
    print("\n【3】开始识别：请对着麦克风连续说几句话（说\"退出\"或按 Ctrl+C 结束）")
    print("-" * 72)

    rec = KaldiRecognizer(model, 16000)
    stream.start_stream()
    sentence_no = 0
    try:
        while True:
            data = stream.read(4096, exception_on_overflow=False)
            # AcceptWaveform 返回 True 表示"一句话结束"，可以取稳定结果
            if rec.AcceptWaveform(data):
                text = json.loads(rec.Result()).get("text", "").strip()
                if text:
                    sentence_no += 1
                    print(f"   第 {sentence_no} 句：{text}")
                    # 说"退出"结束（现场演示时也可以用 Ctrl+C）
                    if text in ("退出", "结束", "再见"):
                        print("   收到结束指令，停止识别")
                        break
                    # ⚠️ 关键点：这里**不 break、不 return**，继续下一轮循环
                    #    —— 这就是 AC-01 要求的"连续多句识别"。
            else:
                # 部分识别结果（还没成句），仅做"正在输入"的实时提示
                partial = json.loads(rec.PartialResult()).get("partial", "").strip()
                if partial:
                    print(f"\r   正在识别… {partial}          ", end="", flush=True)
    except KeyboardInterrupt:
        print("\n   用户中断（Ctrl+C）")
    finally:
        # ---------------- 5. 释放资源（AC-02：停止后必须释放音频流） ----------------
        print("\n【4】释放麦克风与音频流")
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        p.terminate()
        final_text = json.loads(rec.FinalResult()).get("text", "").strip()
        if final_text:
            sentence_no += 1
            print(f"   尾句：{final_text}")
        print(f"   共识别 {sentence_no} 句")

    print("\n" + "=" * 72)
    print("Vosk 自测结束。要点回顾：")
    print("  · 识别循环里只打印结果、不退出 → 支持连续多句（AC-01）")
    print("  · 退出时显式 stop_stream/close/terminate → 释放音频设备（AC-02）")
    print("  · 在 Web 服务里由 VoskAgent.send_msg 只负责发、stop 只负责停，" 
          "识别放在 asyncio.create_task 后台任务中，主协程继续收指令")
    print("=" * 72)


if __name__ == "__main__":
    main()
