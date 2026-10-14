"""
降级容错模块（契约第 5 节，接口锁定 / FR-15 六层降级）

三个降级链（每一级失败都记日志、降级记 WARNING，**永不向上抛异常**）：
    1. invoke_with_fallback     模型：云端主模型 → 云端备用模型 → 本地 Ollama → 静态兜底文案
    2. rag_search_with_fallback 检索：向量检索（Milvus + Rerank） → 关键词检索（MySQL） → 静态建议
    3. call_tool_with_fallback  工具：MCP 远程工具 → 本地同名实现 → Redis 缓存 → 待处理队列

Redis 说明：
    本模块处在"同步降级链路"上（也供 Celery 任务/同步工具复用），
    因此统一使用同步客户端 `redis.StrictRedis(**settings.redis_client_kwargs())`。
    教学/演示规模下直接同步调用即可（TTL 1 小时的少量读写，阻塞可忽略）；
    生产环境应改为 `redis.asyncio` 或 `asyncio.to_thread` 包装，避免阻塞事件循环。
"""
import asyncio
import hashlib
import importlib
import json
import os

from app.core.config import settings
from app.core.logger import logger

# ---------------- langchain 消息类型（可缺失时降级，保证模块仍可导入） ----------------
try:
    from langchain_core.messages import AIMessage
except ImportError as _e:  # pragma: no cover - 未安装 langchain 的教学环境
    logger.warning(f"langchain_core 未安装，fallback 使用占位 AIMessage：{_e}")

    class AIMessage:
        """AIMessage 占位：只保证 content 属性可用"""

        def __init__(self, content: str = "", **_kwargs):
            self.content = content
            self.type = "ai"

        def __repr__(self) -> str:
            return f"AIMessage(content={self.content!r})"

# ---------------- 固定文案（降级链终点） ----------------
STATIC_REPLY = "抱歉，系统繁忙，请稍后再试。如有紧急情况请拨打 120。"
STATIC_ADVICE = "请咨询专业医生获取准确建议。如有紧急情况，请拨打 120。"

# ---------------- 缓存与待处理队列配置 ----------------
TOOL_CACHE_PREFIX = "cache:tool"
TOOL_CACHE_TTL = 3600  # 1 小时
PENDING_TOOLS_KEY = "pending_tools"  # Redis List
PENDING_TOOLS_FILE = "./app/data/pending_tools.json"  # Redis 不可用时的本地降级文件

# 本地工具映射：工具名 → (模块路径, 函数名)。
# 这里只存字符串，真正的 import 放在函数内（延迟导入），
# 保证 mcp_server 尚未就绪 / 未安装时本模块依然可导入。
LOCAL_TOOL_MAP = {
    "get_user_profile": ("mcp_server.tools.profile_tool", "get_user_profile"),
    "get_medication_schedule": ("mcp_server.tools.medication_tool", "get_medication_schedule"),
    "get_vitals": ("mcp_server.tools.vitals_tool", "get_vitals"),
    "search_knowledge": ("mcp_server.tools.knowledge_tool", "search_knowledge"),
    "generate_report": ("mcp_server.tools.report_tool", "generate_report"),
    "send_sms": ("mcp_server.tools.notify_tool", "send_sms"),
    "send_email": ("mcp_server.tools.notify_tool", "send_email"),
    # 常见别名（LLM 可能给出带 _tool 后缀的名字）
    "send_email_tool": ("mcp_server.tools.notify_tool", "send_email"),
    "profile_tool": ("mcp_server.tools.profile_tool", "get_user_profile"),
    "medication_tool": ("mcp_server.tools.medication_tool", "get_medication_schedule"),
    "vitals_tool": ("mcp_server.tools.vitals_tool", "get_vitals"),
    "knowledge_tool": ("mcp_server.tools.knowledge_tool", "search_knowledge"),
    "report_tool": ("mcp_server.tools.report_tool", "generate_report"),
}


# =====================================================================================
# 1. 模型调用降级
# =====================================================================================
async def invoke_with_fallback(messages: list, **kwargs) -> AIMessage:
    """
    模型调用降级链：主模型 → 备用模型 → 本地模型 → 静态兜底文案。

    参数：
        messages: LangChain 消息列表
        **kwargs: 透传给模型 ainvoke 的参数（如 temperature、stream 等）
    返回：
        AIMessage（最差情况返回静态文案，调用方无需 try/except）
    """
    # 三级模型工厂（延迟导入 MyModel，且每级单独 try/except，任何一级异常都不影响下一级）
    levels = (
        ("主模型", "get_model"),
        ("备用模型", "get_backup_model"),
        ("本地模型", "get_local_model"),
    )
    for index, (label, factory_name) in enumerate(levels):
        try:
            from app.ai.model.my_model import MyModel  # 延迟导入，避免循环依赖

            model = getattr(MyModel, factory_name)()
            result = await model.ainvoke(messages, **kwargs)
            logger.info(f"{label}调用成功")
            return result
        except Exception as e:
            logger.error(f"{label}调用失败：{type(e).__name__}: {e}")
            if index < len(levels) - 1:#意思就是到最底层的时候就无法降级了
                logger.warning(f"降级到{levels[index + 1][0]}")

    # ---------------- 最后一级：静态兜底文案 ----------------
    logger.warning("全部模型不可用，返回静态兜底文案")
    return AIMessage(content=STATIC_REPLY)


# =====================================================================================
# 2. RAG 检索降级
# =====================================================================================
def _format_hits(hits: list[dict], top_k: int, mode_cn: str) -> str:
    """把检索结果拼成带来源标注的文本（FR-09 溯源要求）"""
    lines = []
    for index, hit in enumerate(hits[:top_k], start=1):
        if not isinstance(hit, dict):
            continue
        content = str(hit.get("content") or "").strip()
        if not content:
            continue
        source = str(hit.get("source") or "未知来源").strip()
        version = str(hit.get("version") or "").strip()
        page = hit.get("page")
        reference = f"来源：{source}"
        if version:
            reference += f"（{version}）"
        if page not in (None, "", 0):
            reference += f" 第 {page} 页/节"
        lines.append(f"{index}. {content}（{reference}）")
    if not lines:
        return ""
    return f"【{mode_cn}检索到的处置建议】\n" + "\n".join(lines)


def rag_search_with_fallback(query: str, top_k: int = 3) -> str:
    """
    RAG 检索降级链：向量检索（Milvus + Rerank） → 关键词检索（MySQL） → 静态建议。

    返回拼接后的建议文本（带来源标注）；全部失败时返回静态建议，绝不抛异常。
    """
    query = str(query or "").strip()
    if not query:
        return STATIC_ADVICE

    # ---------------- 第 1 级：向量检索 ----------------
    try:
        from app.ai.tool.rag_tool import rag_search

        hits = rag_search(query, top_k)
        text = _format_hits(hits or [], top_k, "向量")
        if text:
            logger.info(f"向量检索命中 {len(hits)} 条：{query[:30]}")
            return text
        logger.warning("向量检索无结果，降级为关键词检索")
    except Exception as e:
        logger.error(f"向量检索失败：{type(e).__name__}: {e}，降级为关键词检索")

    # ---------------- 第 2 级：关键词检索 ----------------
    logger.warning("降级为关键词检索")
    try:
        from app.ai.tool.rag_tool import keyword_search

        hits = keyword_search(query, top_k)
        text = _format_hits(hits or [], top_k, "关键词")
        if text:
            logger.info(f"关键词检索命中 {len(hits)} 条：{query[:30]}")
            return text
        logger.warning("关键词检索同样无结果，降级为静态建议")
    except Exception as e:
        logger.error(f"关键词检索失败：{type(e).__name__}: {e}，降级为静态建议")

    # ---------------- 第 3 级：静态建议 ----------------
    logger.warning("全部检索通道不可用，返回静态建议")
    return STATIC_ADVICE


# =====================================================================================
# 3. 工具调用降级
# =====================================================================================
def get_sync_redis():
    """
    创建同步 Redis 客户端（降级链路专用）。
    未安装 redis / 连接参数非法时返回 None，由调用方走本地文件降级。
    """
    try:
        import redis  # 延迟导入

        client = redis.StrictRedis(**settings.redis_client_kwargs())
        client.ping()  # 立即探活，避免"拿到客户端但实际不可用"
        return client
    except ImportError:
        logger.warning("未安装 redis，工具缓存与队列降级为本地文件")
        return None
    except Exception as e:
        logger.warning(f"Redis 不可用，工具缓存与队列降级为本地文件：{e}")
        return None


def _cache_key(name: str, arguments: dict) -> str:
    """缓存键：cache:tool:{name}:{参数哈希}（同样入参复用同一结果）"""
    try:
        raw = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = str(arguments)
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
    return f"{TOOL_CACHE_PREFIX}:{name}:{digest}"


def _cache_set(key: str, payload: dict) -> bool:
    """写缓存（TTL 1 小时）；失败只记日志"""
    client = get_sync_redis()
    if client is None:
        return False
    try:
        client.setex(key, TOOL_CACHE_TTL, json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as e:
        logger.error(f"写工具缓存失败：{e}")
        return False


def _cache_get(key: str) -> dict | None:
    """读缓存；未命中或 Redis 不可用返回 None"""
    client = get_sync_redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.error(f"读工具缓存失败：{e}")
        return None


def _load_local_tool(name: str):
    """函数内延迟导入本地同名工具实现；不可用返回 None"""
    entry = LOCAL_TOOL_MAP.get(name)
    if not entry:
        return None
    module_path, func_name = entry
    try:
        module = importlib.import_module(module_path)
        func = getattr(module, func_name, None)
        return func if callable(func) else None
    except ImportError as e:
        logger.warning(f"本地工具实现不可用（{module_path}）：{e}")
        return None
    except Exception as e:
        logger.error(f"加载本地工具异常（{module_path}）：{e}")
        return None


def _call_local_tool(func, arguments: dict):
    """调用本地工具：优先关键字传参，失败退回位置传参"""
    args = arguments if isinstance(arguments, dict) else {}
    try:
        return func(**args)
    except TypeError:
        return func(*args.values())


def push_pending_tool(name: str, arguments: dict, reason: str = "") -> str:
    """
    把失败的调用写入"待处理队列"：Redis List `pending_tools` 优先，
    不可用时写本地 JSON 文件 ./app/data/pending_tools.json。返回落地位置标识。
    """
    record = {
        "tool": name,
        "arguments": arguments if isinstance(arguments, dict) else {},
        "reason": reason,
        "ts": _now_text(),
    }
    # ---------------- Redis 队列 ----------------
    client = get_sync_redis()
    if client is not None:
        try:
            client.rpush(PENDING_TOOLS_KEY, json.dumps(record, ensure_ascii=False))
            logger.warning(f"工具 {name} 调用失败，已写入 Redis 待处理队列 {PENDING_TOOLS_KEY}")
            return "redis"
        except Exception as e:
            logger.error(f"写 Redis 待处理队列失败，降级为本地文件：{e}")

    # ---------------- 本地文件（降级） ----------------
    try:
        directory = os.path.dirname(os.path.abspath(PENDING_TOOLS_FILE))
        os.makedirs(directory, exist_ok=True)
        items = []
        if os.path.isfile(PENDING_TOOLS_FILE):
            try:
                with open(PENDING_TOOLS_FILE, "r", encoding="utf-8") as file:
                    loaded = json.load(file)
                if isinstance(loaded, list):
                    items = loaded
            except (json.JSONDecodeError, OSError):
                items = []  # 文件损坏则重建，避免阻塞后续写入
        items.append(record)
        with open(PENDING_TOOLS_FILE, "w", encoding="utf-8") as file:
            json.dump(items, file, ensure_ascii=False, indent=2)
        logger.warning(f"工具 {name} 调用失败，已写入本地待处理文件 {PENDING_TOOLS_FILE}")
        return "file"
    except Exception as e:
        logger.error(f"写本地待处理文件失败：{e}")
        return "failed"


def _now_text() -> str:
    """当前时间字符串（仅用于留痕）"""
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def call_tool_with_fallback(name: str, arguments: dict) -> dict:
    """
    工具调用降级链：MCP 远程 → 本地实现 → 缓存 → 待处理队列。

    返回统一结构 {"ok": bool, "data":..., "msg": str, "source": "mcp|local|cache|pending"}
    说明：教学规模下 Redis/本地文件均为同步调用（见模块说明），不阻塞主流程的关键路径。
    """
    arguments = arguments if isinstance(arguments, dict) else {}
    cache_key = _cache_key(name, arguments)

    # ---------------- 第 1 级：MCP 远程工具 ----------------
    try:
        from app.ai.tool.mcp_tool import call_mcp_tool

        result = await call_mcp_tool(name, arguments)
        if isinstance(result, dict) and result.get("ok"):
            payload = {
                "ok": True,
                "data": result.get("data", result),
                "msg": result.get("msg") or "MCP 工具调用成功",
                "source": "mcp",
            }
            _cache_set(cache_key, payload)  # 成功结果写缓存（TTL 1 小时）
            return payload
        logger.error(f"MCP 工具 {name} 返回失败：{result}")
    except Exception as e:
        logger.error(f"MCP 工具 {name} 调用异常：{type(e).__name__}: {e}")

    # ---------------- 第 2 级：本地同名实现 ----------------
    logger.warning(f"MCP 工具 {name} 不可用，降级为本地实现")
    try:
        func = _load_local_tool(name)
        if func is not None:
            data = _call_local_tool(func, arguments)
            payload = {"ok": True, "data": data, "msg": "本地工具兜底结果", "source": "local"}
            _cache_set(cache_key, payload)
            return payload
        logger.error(f"未找到本地实现：{name}")
    except Exception as e:
        logger.error(f"本地工具 {name} 执行异常：{type(e).__name__}: {e}")

    # ---------------- 第 3 级：缓存结果 ----------------
    logger.warning(f"本地实现不可用，尝试读取工具缓存：{name}")
    cached = _cache_get(cache_key)
    if cached:
        return {
            "ok": True,
            "data": cached.get("data"),
            "msg": "工具暂不可用，返回缓存结果",
            "source": "cache",
        }

    # ---------------- 第 4 级：待处理队列 ----------------
    logger.warning(f"工具 {name} 全部通道失败，写入待处理队列")
    location = push_pending_tool(name, arguments, reason="MCP/本地/缓存均不可用")
    return {
        "ok": False,
        "msg": "工具暂不可用，已加入待处理队列",
        "source": "pending",
        "queue": location,
    }


if __name__ == "__main__":
    # 自测：三条链路在"依赖全缺"的情况下也必须返回可用结果（不抛异常）
    async def _main():
        logger.info(f"静态文案：{STATIC_REPLY}")
        logger.info(f"检索降级结果：{rag_search_with_fallback('血压高怎么办')}")
        tool_result = await call_tool_with_fallback("get_vitals", {"user_id": "u001", "days": 7})
        logger.info(f"工具降级结果：{tool_result}")
        message = await invoke_with_fallback([{"role": "user", "content": "你好"}])
        logger.info(f"模型降级结果：{getattr(message, 'content', message)}")

    asyncio.run(_main())
