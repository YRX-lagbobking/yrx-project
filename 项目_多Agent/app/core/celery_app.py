"""
Celery 应用单例（契约第 20 节）

Celery 是本项目的必装依赖：broker / backend 都用 Redis（settings.redis_url），不做降级处理。

启动命令（Windows 必须 -P solo）：
    celery -A app.core.celery_app.celery_app worker -l info -P solo
    celery -A app.core.celery_app.celery_app beat   -l info
"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "health_butler",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # ★ 关键：worker 启动时导入任务模块，否则任务未注册 → NotRegistered
    include=[
        "app.core.task.detect_task",
        "app.core.task.notify_task",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=False,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "auto-detect": {
            "task": "auto_detect_all",   # ★ 必须与 @celery_app.task(name="auto_detect_all") 一致，否则 NotRegistered
            "schedule": settings.detect_interval_minutes * 60,
        }
    },
)


if __name__ == "__main__":
    print("broker/backend:", settings.redis_url)
    print("beat_schedule:", celery_app.conf.beat_schedule)
