"""
家属通知自测：Celery 异步任务 + 本地兜底队列（FR-07 / AC-11、AC-12）

推荐运行方式（在项目根目录下执行）：
    python -m app.test.test_notify
    python app/test/test_notify.py

演示内容：
    1. notify_family_async(user_id, level, content, channel) —— 主流程调用入口：
       内部走 Celery .delay() 投递到 Redis broker，由 Worker 进程异步发送；
       失败按 max_retries=3 / 60s 重试（NFR-02 / AC-12）；
    2. 直接调用 Celery 任务 notify_family(...)，观察返回结构；
    3. 关闭短信通道场景（AUTO_NOTIFY_SMS=false）的说明：不直接发短信，
       置 need_human_confirm=True，等家属 /alert/{id}/confirm 后再发（AC-12）；
    4. 双通道告警：通道 1 MCP 短信/邮件 → 家属；通道 2 WebSocket → 老人端弹窗+播报。

依赖：celery、redis（可选）。缺失时打印降级路径并继续。
注意：本脚本不会真的发短信/邮件（除非 .env 配好 EMAIL_* 且通道可用）。
"""
import os
import sys

# ---------- 项目根目录加入 sys.path ----------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def show_family_contact(user_id: str) -> None:
    """打印该老人的紧急联系人与绑定家属（只读，失败即跳过）"""
    try:
        from app.dao.user_dao import UserDAO

        contact = UserDAO.get_emergency_contact(user_id)
        print(f"     紧急联系人：{contact}")
        families = UserDAO.list_family(user_id)
        print(f"     绑定家属（{len(families)} 位）："
              f"{[f.get('user_name') or f.get('user_id') for f in families]}")
    except Exception as e:
        print(f"     [跳过] 查询家属失败（MySQL 未启动或 DAO 未完成）：{e}")


def main() -> None:
    print("=" * 72)
    print("家属通知自测：Celery 异步任务 + 本地兜底队列（FR-07 / AC-11、AC-12）")
    print("=" * 72)

    # ---------------- 1. 导入 ----------------
    try:
        from app.core.task.notify_task import notify_family, notify_family_async
    except Exception as e:
        print(f"[跳过] app/core/task/notify_task.py 不可用：{e}")
        print("       请先完成 core-dao 模块（契约第 20 节），再运行本脚本。")
        return

    try:
        from app.core.config import settings

        print(f"\n【0】通知配置：AUTO_NOTIFY_SMS={settings.auto_notify_sms} "
              f"REDIS_URL={settings.redis_url}")
        print(f"       MCP Server={settings.mcp_server_url} "
              f"EMAIL_HOST={settings.email_host} EMAIL_TO={settings.email_to or '(未配置)'}")
    except Exception:
        settings = None

    user_id = "u001"
    print(f"\n【1】目标老人 {user_id} 的通知对象：")
    show_family_contact(user_id)

    # ---------------- 2. 主流程入口：notify_family_async ----------------
    print("\n" + "-" * 72)
    print("【2】notify_family_async('u001', '高危', '...', channel='sms')")
    print("     预期：走 Celery .delay() 投递到 Redis broker，由 Worker 异步发送（AC-12）")
    content = "检测到您的家人张三收缩压达到 186 mmHg，属于高危异常，请尽快联系并确认其身体状况。"
    try:
        result = notify_family_async(user_id, "高危", content, channel="sms")
        print(f"     返回：{result}")
        if isinstance(result, dict):
            if result.get("ok"):
                print(f"     ✔ 已投递到 Celery 队列（channel={result.get('channel')}），"
                      "由 Worker 异步发送（通道 1）")
                print("       注意：投递成功 ≠ 已发送，真正发送在 Worker 进程内完成。")
            else:
                print(f"     ℹ️ channel={result.get('channel')}，msg={result.get('msg')}")

        # 查看 Celery broker 队列长度（只增不减 = Worker 没在消费）
        try:
            import redis

            r = redis.StrictRedis(**settings.redis_client_kwargs()) if settings else None
            if r is not None:
                depth = r.llen("celery")
                print(f"     Celery broker 队列 celery 长度 = {depth}（>0 且不下降说明 Worker 未消费）")
        except Exception as e:
            print(f"     [提示] Redis 不可用，无法查看 Celery 队列：{type(e).__name__}: {e}")
            # 模拟短信通道自己还有本地文件兜底（pending_sms.json）
            try:
                import os as _os

                data_dir = _os.path.join(
                    _os.path.dirname(_os.path.dirname(_os.path.dirname(
                        _os.path.dirname(_os.path.abspath(__file__))))),
                    "app", "data",
                )
                sms_file = _os.path.join(data_dir, "pending_sms.json")
                if _os.path.isfile(sms_file):
                    print(f"     本地文件队列存在：{sms_file}（{_os.path.getsize(sms_file)} 字节）")
            except Exception:
                pass
    except Exception as e:
        print(f"     [降级] notify_family_async 调用失败：{type(e).__name__}: {e}")

    # ---------------- 3. 直接调用 Celery 任务 ----------------
    print("\n" + "-" * 72)
    print("【3】直接调用 Celery 任务 notify_family(user_id, level, content, channel)")
    try:
        result = notify_family.run(user_id, "中危", content, channel="email")
        print(f"     同步执行返回：{result}")
    except AttributeError as e:
        # Celery 未安装时 notify_family 是普通函数，没有 .run 属性
        print(f"     ℹ️ 该环境未安装 Celery，notify_family 退化为普通函数（{e}）")
        try:
            result = notify_family(user_id, "中危", content, channel="email")
            print(f"     普通调用返回：{result}")
        except Exception as e2:
            print(f"     [降级] 调用失败：{type(e2).__name__}: {e2}")
    except Exception as e:
        print(f"     [降级] 调用失败（通常是 Redis 或 SMTP 不可用）："
              f"{type(e).__name__}: {e}")

    # ---------------- 4. 双通道与重试策略 ----------------
    print("\n" + "-" * 72)
    print("【4】双通道告警与重试策略（AC-11）")
    print("     通道 1：Celery 异步任务 → MCP send_sms / send_email → 家属")
    print("             失败按 max_retries=3, default_retry_delay=60s 重试；")
    print("             全失败写日志并返回失败结果（模拟短信通道自身还有 pending_sms 队列）。")
    print("     通道 2：WebSocket 推送到老人端 {type:'health_alert', level, message, action:'voice_play'}")
    print("             → 前端弹窗 + EdgeTTS 语音播报 + \"我已知道\"回执（POST /alert/{id}/confirm）。")
    print("     并发方式：asyncio.gather 同时触发两条通道，互不阻塞主流程。")

    # ---------------- 5. AC-12 人工确认场景 ----------------
    print("\n" + "-" * 72)
    print("【5】AC-12 人工确认场景（AUTO_NOTIFY_SMS=false）")
    if settings is not None and not settings.auto_notify_sms:
        print("     当前 .env 已关闭自动短信 → 高危告警不直接发出，")
        print("     节点置 need_human_confirm=True，等家属确认后再发。")
    else:
        print("     当前 AUTO_NOTIFY_SMS=True（默认）：高危立即通知。")
    print("     验证方式：把 AUTO_NOTIFY_SMS 改为 false 后重启服务，触发一次高危检测，")
    print("               应看到 need_human_confirm=true 且 alert_record 状态为\"待确认\"，")
    print("               调用 POST /alert/{id}/confirm 后才发出短信/邮件。")

    print("\n" + "=" * 72)
    print("家属通知自测结束。要点回顾：")
    print("  · 主流程只调 notify_family_async，不阻塞；耗时 IO 交给 Celery")
    print("  · 五级兜底：短信 → 邮件 → 备用网关 → Celery 重试 → 本地队列（NFR-02）")
    print("  · 全程留痕：alert_record.notify_result + 日志 WARNING/CRITICAL")
    print("=" * 72)


if __name__ == "__main__":
    main()
