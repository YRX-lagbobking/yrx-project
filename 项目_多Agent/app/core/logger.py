"""
统一日志模块（契约第 3 节，接口锁定）

设计说明：
    1. 全项目统一 `from app.core.logger import logger`，禁止裸 print 作为唯一输出；
    2. 日志格式固定为 "[时间] [级别] [模块] 消息"，便于 grep 与教学演示；
    3. 重复 import 时（例如 uvicorn reload、Celery worker fork）不得重复添加 Handler，
       否则同一条日志会打印多次——因此用 `if not logger.handlers` 做幂等保护。
"""
import logging
import sys

# 日志格式：[2025-01-01 12:00:00] [INFO] [manager_node] 消息
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 控制台编码兜底：Windows 下控制台编码可能是 cp1252/GBK，
# 打印中文日志时可能抛 UnicodeEncodeError 把业务代码打断。
# 这里只把错误策略改成 replace（不强制改编码，避免中文在 GBK 终端变乱码）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:  # 非 TextIOWrapper（如被重定向）时忽略
        pass

# 全局唯一 logger 实例（与契约第 3 节逐字一致）
logger = logging.getLogger("health_butler")

# 幂等配置：只有当前没有任何 Handler 时才添加，避免重复输出
if not logger.handlers:
    # 控制台输出（容器 / 本地终端）
    _console_handler = logging.StreamHandler(sys.stdout)
    _console_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    _console_handler.setLevel(logging.INFO)
    logger.addHandler(_console_handler)

    # 默认级别；request 级 DEBUG 日志需要显式开启
    logger.setLevel(logging.INFO)

    # 阻断向 root logger 冒泡，避免 uvicorn 的 root 配置造成重复行
    logger.propagate = False


def get_logger(name: str = "health_butler") -> logging.Logger:
    """
    获取带统一格式的 logger。

    参数：
        name: logger 名称；传 "health_butler" 时直接返回全局单例，
              传其它名字时返回其子 logger（继承父级 Handler，无需重复配置）。
    """
    if name == "health_butler":
        return logger
    # 子 logger 继承 health_butler 的 Handler 与级别，避免每个模块重复挂 Handler
    child = logging.getLogger(name)
    if not child.handlers:
        # 只设级别，不重复添加 Handler（交给父 logger 输出）
        child.setLevel(logging.NOTSET)
    return child


if __name__ == "__main__":
    # 自测：验证格式、级别与幂等性
    logger.info("日志模块自测：这是一条 INFO")
    logger.warning("日志模块自测：这是一条 WARNING")
    logger.error("日志模块自测：这是一条 ERROR")
    get_logger("health_butler.detect").info("子 logger 输出正常")
    logger.info(f"当前 Handler 数量：{len(logger.handlers)}（重复导入后应仍为 1）")
