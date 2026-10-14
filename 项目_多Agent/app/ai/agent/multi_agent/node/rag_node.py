"""
RAG 节点：健康知识检索与溯源（FR-09 / AC-14、AC-15）

流程（三级降级，永不因检索失败而中断主流程）：
    1. 拼检索 query：用户问题 + 体征摘要（体征让"血压偏高该注意什么"这类问题检索更准）；
    2. 从健康档案取 disease_history 作为 `disease_type` 元数据过滤条件
       （HealthDAO 是同步函数，教学规模直接在 async 节点内调用，生产应改连接池/异步）；
    3. `rag_search(query, top_k, disease_type)` 向量检索（Milvus 召回 + Rerank 精排）；
    4. 失败或无结果 → `keyword_search(query, top_k)` 关键词检索（MySQL knowledge_doc）并记 WARNING（AC-15）；
    5. 再失败 → knowledge=[] 并提示"知识库暂不可用，请咨询专业医生"。

**溯源要求（AC-14）**：每个知识片段必须带 source / version / page，供前端展开"依据"。
写入状态：knowledge、task_results["knowledge"]、health_step="rag"，并追一条 AIMessage。
"""
from langchain.messages import HumanMessage
from langchain_core.messages import AIMessage

from app.ai.agent.multi_agent.state.health_state import HealthState
from app.ai.tool.rag_tool import keyword_search, rag_search
from app.core.logger import logger
from app.dao.health_dao import HealthDAO

# 检索条数（Top-K，精排后再截断）
TOP_K = 5
# 体征摘要里展示的字段（中文名 + 键名）
VITAL_SUMMARY_FIELDS = (
    ("心率", "heart_rate"),
    ("收缩压", "blood_pressure_sys"),
    ("舒张压", "blood_pressure_dia"),
    ("血氧", "spo2"),
    ("体温", "temperature"),
    ("血糖", "glucose"),
    ("睡眠", "sleep_hours"),
)


def _extract_question(state: HealthState) -> str:
    """从消息历史里取本轮用户问题（最后一条 HumanMessage，兜底取最后一条消息）"""
    messages = state.get("messages") or []
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content or "")
    if messages:
        return str(getattr(messages[-1], "content", "") or "")
    return ""


def _vitals_summary(vitals: dict) -> str:
    """把体征字典拼成一句中文摘要，用于拼检索 query"""
    if not vitals:
        return ""
    parts = []
    for cn, key in VITAL_SUMMARY_FIELDS:
        value = vitals.get(key)
        if value is None or value == "":
            continue
        parts.append(f"{cn}{value}")
    return "，".join(parts)


def _normalize_items(items) -> list[dict]:
    """
    规范化检索结果，**强制补齐溯源四要素**（content/source/version/page/score）。
    缺失的元数据用"未知来源"占位，保证前端展示结构稳定（不会因为缺键而报错）。
    """
    result: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        result.append(
            {
                "content": content,
                "source": str(item.get("source") or "未知来源"),
                "version": str(item.get("version") or "未标注版本"),
                "page": item.get("page") if item.get("page") is not None else "未标注页码",
                "score": item.get("score", 0.0),
            }
        )
    return result


def _disease_type(user_id: str) -> str | None:
    """取健康档案里的慢病史作为疾病类型过滤条件；失败返回 None（不过滤）"""
    try:
        profile = HealthDAO.get_profile(user_id) or {}
        disease = profile.get("disease_history")
        if not disease:
            return None
        # disease_history 可能是"高血压,糖尿病"这种逗号串，取第一种做主过滤
        first = str(disease).replace("，", ",").split(",")[0].strip()
        return first or None
    except Exception as e:
        logger.error(f"[RAG] 读取健康档案失败，跳过疾病类型过滤：{e}")
        return None


async def rag_node(state: HealthState):
    """知识检索节点：向量优先 → 关键词降级 → 静态提示"""
    user_id = str(state.get("user_id") or "")
    question = _extract_question(state)
    vitals = state.get("vitals") or {}
    knowledge: list[dict] = []
    try:
        # 1. 拼检索 query（问题 + 体征摘要）
        summary = _vitals_summary(vitals)
        query = f"{question}（当前体征：{summary}）" if summary else question
        # 2. 疾病类型过滤（个体化检索，避免检索到与本人慢病无关的内容）
        disease_type = _disease_type(user_id)
        logger.info(f"[RAG] 检索 query={query} | disease_type={disease_type}")

        # 3. 向量检索（Milvus + Rerank），内置重试与降级
        try:
            knowledge = _normalize_items(rag_search(query, top_k=TOP_K, disease_type=disease_type))
        except Exception as e:
            logger.warning(f"[RAG] 向量检索不可用，降级关键词检索：{e}")
            knowledge = []

        # 4. 向量检索无结果 → 关键词检索降级（AC-15）
        if not knowledge:
            logger.warning("[RAG] 向量检索无结果，降级为关键词检索 keyword_search")
            try:
                knowledge = _normalize_items(keyword_search(query, top_k=TOP_K))
            except Exception as e:
                logger.error(f"[RAG] 关键词检索也失败，知识库暂不可用：{e}")
                knowledge = []

        # 5. 组织说明消息（溯源信息一并写进消息，方便调试与答辩定位）
        if knowledge:
            sources = []
            for item in knowledge:
                tag = f"{item['source']}(v{item['version']}, p{item['page']})"
                if tag not in sources:
                    sources.append(tag)
            note = f"已检索到 {len(knowledge)} 条知识，来源：{'；'.join(sources)}"
        else:
            note = "知识库暂不可用，请咨询专业医生；如身体不适请立即联系家属或拨打 120。"

        return {
            "knowledge": knowledge,
            "task_results": {"knowledge": {"status": "ok", "count": len(knowledge), "knowledge": knowledge}},
            "health_step": "rag",
            "messages": [AIMessage(content=note)],
        }
    except Exception as e:
        # 异常兜底：写 error 让 Manager 自动重试（超过 3 次降级 chat）；
        # 说明：检索到 0 条不算失败（status="ok"），只有节点本身异常才标记 error，
        # 避免"知识库暂时没命中"被误判为任务失败而反复重试。
        logger.error(f"[RAG] 知识检索节点异常：{e}")
        return {
            "knowledge": [],
            "task_results": {"knowledge": {"status": "error", "msg": str(e)}},
            "health_step": "rag",
            "messages": [AIMessage(content="知识库暂不可用，请咨询专业医生；如身体不适请立即联系家属或拨打 120。")],
        }


if __name__ == "__main__":
    # 自测说明：只验证纯函数（摘要拼接 / 结果规范化），不连 Milvus 与 MySQL。
    print("体征摘要：", _vitals_summary({"heart_rate": 78, "blood_pressure_sys": 135, "spo2": 96}))
    print("规范化：", _normalize_items([{"content": "高血压患者应低盐饮食", "source": "高血压防治指南"}]))
    # 真连知识库自测：
    # import asyncio
    # async def test():
    #     print(await rag_node({"user_id": "u001", "messages": [], "vitals": {"blood_pressure_sys": 150}}))
    # asyncio.run(test())
