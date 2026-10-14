"""
智能健康管家多智能体系统 —— 应用入口

启动流程（lifespan）：
    1. 选择 Checkpointer：postgres（生产，AsyncPostgresSaver + 自动建表）/
       memory（开发调试，InMemorySaver）；Postgres 不可用时自动降级为内存版；
    2. 建立 Redis 异步客户端（窗口记忆 / 画像 / 会话 / 分布式锁）；
    3. 创建 HealthGraphAgent 并挂到 app.state.health_agent，供路由层取用；
    4. 打开摘要记忆连接池（PostgreSQL）；
    5. 启动补历史 + 数据模拟器（保证"数据持续产生、服务不常驻"下序列连续）；
    6. 应用关闭时释放资源。

设计说明：以上每一步失败都只降级、不阻断启动（NFR-02）。
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager

# ============================================================================
# 统一把工作目录切到项目根（治本，防止"CWD 不是项目根"导致启动即崩）
# ----------------------------------------------------------------------------
# 为什么需要：项目里有多处相对路径 —— ./app/html（静态目录）、app/html/tts（TTS 产物）、
#   app/html/reports（报告）、./app/data/reports（Word 导出）、./app/data/pending_*.json（降级队列）、
#   ./knowledge_docs（灌库与 BM25 语料）、./app/chroma_db（长期记忆降级）——
#   它们全部按**进程工作目录(CWD)**解析，而不按脚本位置。
# 踩过的坑：PyCharm 点 Run 时运行配置的 Working directory 默认 = 脚本所在目录(app/)，
#   于是 "./app/html" 被解析成 "app/app/html"，StaticFiles 构造即抛
#   RuntimeError: Directory './app/html' does not exist（启动即崩）。
#   注意：IDE 里的"内容根/源码根"只影响 import（PYTHONPATH），不影响 CWD！
# 做法：入口处主动校正 CWD（Django/Flask 项目也常用），一次修好全部相对路径，
#   命令行 / IDE / 任意目录启动都能正常起来。
# 位置：必须放在自身 import 之后、任何 app.* 模块导入之前，
#   避免子模块在"导入期"就按旧 CWD 计算路径。
# ============================================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.ai.agent.multi_agent.graph.health_graph_agent import HealthGraphAgent
from app.core.config import settings
from app.core.logger import logger
from app.web.alert_router.alert_router import alert_router
from app.web.chat_router.chat_router import chat_router
from app.web.default_page_router.default_page_router import default_page_router
from app.web.report_router.report_router import report_router
from app.web.system_router.system_router import system_router
from app.web.user_router.user_router import user_router
from app.web.voice_router.voice_router import voice_router
from app.web.websocker_router.websocket_notify import ws_manager, ws_router


async def _build_checkpointer():
    """
    构建会话状态存储器。
    返回 (saver, 上下文管理器)；上下文管理器为 None 表示不需要手动释放。
    """
    if settings.checkpointer == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        logger.info("使用 InMemorySaver（开发调试，重启即丢）")
        return InMemorySaver(), None

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        cm = AsyncPostgresSaver.from_conn_string(settings.postgresql_url)
        saver = await cm.__aenter__()
        await saver.setup()  # 幂等，首次会建表
        logger.info("使用 AsyncPostgresSaver（生产，跨重启恢复记忆）")
        return saver, cm
    except Exception as e:
        logger.error(f"Postgres Checkpointer 不可用，降级为 InMemorySaver：{e}")
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver(), None


async def _build_redis():
    """建立 Redis 异步客户端；不可用时返回 None（会话校验与记忆自动降级）"""
    try:
        import redis.asyncio as aioredis

        client = aioredis.StrictRedis(**settings.redis_client_kwargs())
        await client.ping()
        logger.info(f"Redis 连接成功：{settings.redis_host}:{settings.redis_port}")
        return client
    except Exception as e:
        logger.error(f"Redis 不可用，窗口记忆/会话锁将降级：{e}")
        return None


async def _open_memory_pool():
    """打开摘要记忆（PostgreSQL）连接池"""
    try:
        from app.ai.agent.memory.save.summary_memory import pool

        if pool is not None:
            await pool.open()
            logger.info("摘要记忆连接池已打开")
    except Exception as e:
        logger.error(f"摘要记忆连接池打开失败（降级）：{e}")


async def _close_memory_pool():
    """关闭摘要记忆连接池"""
    try:
        from app.ai.agent.memory.save.summary_memory import pool

        if pool is not None:
            await pool.close()
            logger.info("摘要记忆连接池已关闭")
    except Exception as e:
        logger.error(f"摘要记忆连接池关闭异常：{e}")


def _start_vitals_data():
    """
    生命体征数据初始化
    启动补历史 + 运行期数据模拟

    数据产生的持续性策略：
        · VITALS_BACKFILL_DAYS > 0 → 启动按 VITALS_INTERVAL_MINUTES 粒度回填对应天数；
        · VITALS_BACKFILL_DAYS = 0 → **跳过补历史**（服务立即就绪），
          数据完全由运行期模拟器（启动即写第一轮）或外部脚本提供。
    """
    try:
        from app.ai.tool.influx_tool import is_available, start_simulator, write_history
        from app.dao.user_dao import UserDAO

        if not is_available():
            logger.warning("InfluxDB 不可用，跳过生命体征数据初始化")
            return
        elders = UserDAO.list_elders()
        if not elders:
            logger.warning("没有老人用户，跳过生命体征数据初始化")
            return
        # 补历史（VITALS_BACKFILL_DAYS=0 时跳过：启动更快）
        if settings.vitals_backfill_days > 0:
            for user_id in elders:
                count = write_history(
                    user_id, settings.vitals_backfill_days, settings.vitals_interval_minutes
                )
                logger.info(f"用户 {user_id} 补历史 {count} 条生命体征数据")
        else:
            logger.info("VITALS_BACKFILL_DAYS=0，跳过启动补历史（数据由运行期模拟器持续写入）")
        # 运行期常驻写入（启动即写第一轮，之后每 VITALS_INTERVAL_MINUTES 分钟一轮）
        start_simulator(elders)
    except Exception as e:
        logger.error(f"生命体征数据初始化失败（不影响服务启动）：{e}")


# 定义异步上下文管理器
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用启动时：建立连接、创建智能体、启动数据管道
    应用关闭时：自动释放连接池
    """
    logger.info("========== 智能健康管家启动中 ==========")
    # 1. Checkpointer
    saver, saver_cm = await _build_checkpointer()
    # 2. Redis
    r = await _build_redis()
    # 3. 多智能体
    app.state.health_agent = HealthGraphAgent(saver, r)
    app.state.ws_manager = ws_manager
    logger.info("智能健康管家多智能体创建成功")
    # 4. 摘要记忆连接池
    await _open_memory_pool()
    # 5. 生命体征数据管道
    _start_vitals_data()
    logger.info("========== 启动完成，接口地址 http://localhost:8000 ==========")
    try:
        yield
    finally:
        # 释放资源
        app.state.health_agent = None
        logger.info("智能健康管家多智能体释放成功")
        await _close_memory_pool()
        if saver_cm is not None:
            try:
                await saver_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.error(f"Checkpointer 释放异常：{e}")
        if r is not None:
            try:
                await r.aclose()
            except Exception:
                pass


app = FastAPI(
    title="智能健康管家多智能体系统",
    description="面向独居老人居家健康管理：语音交互 + 健康问询 + 用药提醒 + 风险预警 + 家属通知",
    version="1.0.0",
    lifespan=lifespan,
)

# 跨域（生产应改为白名单）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(system_router)
app.include_router(user_router)
app.include_router(chat_router)
app.include_router(voice_router)
app.include_router(report_router)
app.include_router(alert_router)
app.include_router(ws_router)
app.include_router(default_page_router)

# 配置静态资源访问目录（前端页面 + TTS 语音产物）
# 静态资源目录：用 PROJECT_ROOT 拼绝对路径（不再依赖当前工作目录）
app.mount("/static", StaticFiles(directory=os.path.join(PROJECT_ROOT, "app", "html")), name="static")


if __name__ == "__main__":
    # ============================================================================
    # Windows 下必须让服务跑在 SelectorEventLoop 上（Vosk/PyAudio + psycopg 都要求它）
    # ----------------------------------------------------------------------------
    # 为什么不能只写 set_event_loop_policy()：
    #   uvicorn 0.36+ 改成用 loop_factory **显式构造**事件循环，不再读 event_loop_policy
    #   （uvicorn/server.py: asyncio_run(self.serve(), loop_factory=config.get_loop_factory())），
    #   而 uvicorn/loops/asyncio.py 的 asyncio_loop_factory 在 win32 且非子进程模式下
    #   直接返回 asyncio.ProactorEventLoop。于是 policy 变成了"没人读的开关"，实际跑 Proactor。
    # 为什么必须换掉 Proactor：
    #   psycopg 3 的**异步**连接拒绝在 ProactorEventLoop 上运行
    #   （IOCP 不支持 add_reader/add_writer），会抛
    #   InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode。
    #   受害者有两个：
    #     · LangGraph 的 AsyncPostgresSaver（Postgres Checkpointer）→ 降级 InMemorySaver
    #       → 会话状态重启即丢；
    #     · L2 摘要记忆的 AsyncConnectionPool → 降级进程内字典；
    #   同时 Vosk / PyAudio 在 Windows 上也要求 SelectorEventLoop。
    # 做法：自己建一个 SelectorEventLoop，直接等待 uvicorn.Server.serve()，
    #       完全绕过 uvicorn 的 loop_factory。
    # 生产部署（Linux/Docker）没有该限制，走下面的 else 分支即可。
    # ============================================================================
    if sys.platform == "win32":
        import selectors

        config = uvicorn.Config("app.main:app", host="0.0.0.0", port=8000, reload=False)
        server = uvicorn.Server(config)
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        print("[启动] uvicorn 运行在 SelectorEventLoop（psycopg / Vosk 兼容；非 Proactor）")
        try:
            loop.run_until_complete(server.serve())
        except KeyboardInterrupt:
            pass
        finally:
            loop.close()
    else:
        uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
