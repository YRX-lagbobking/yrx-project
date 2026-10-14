"""
异步任务包（Celery）：自动检测与家属通知（契约第 20 节）。

    notify_task.py  家属通知任务（Celery 任务 + 主流程同步入口）
    detect_task.py  自动检测任务（Beat 定时全量检测 + 单用户双通道闭环）

说明：Celery 为必装依赖（Redis 作 broker/backend）；
     检测任务也可用 asyncio 直接调用做本地演练。
"""
from app.core.task.detect_task import auto_detect_all, auto_detect_once
from app.core.task.notify_task import notify_family, notify_family_async

__all__ = ["notify_family", "notify_family_async", "auto_detect_all", "auto_detect_once"]
