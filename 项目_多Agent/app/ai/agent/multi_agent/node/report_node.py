"""
Report 节点（最简版）：日报 / 周报 —— **Skill 驱动 + 一次大模型调用**

分工（一句话）：
    · **代码**只做两件事：取数据（体征/告警/用药/档案 → 一段纯文本）、落盘；
    · **SKILL.md** 规定"报告怎么写"（四章节结构、语气、必须保留 RAG 溯源）；
    · **模型**读【数据 + 技能规程】后流式生成正文 —— 全程只调一次 create_agent。

对比旧版删掉的东西（都是为了简单）：
    ✗ ReportSchema 结构化解析（`_parse_schema` + parser_agent 第二次模型调用）
    ✗ 四章节模板渲染器（`_brief_sections` / `_render`）
    → 章节结构由 SKILL.md 保证，不在代码里再拼一遍。

写入状态：report_type、report_content、report_path、task_results["report"]、health_step="report"。
"""
import os
from datetime import datetime

from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer

from app.ai.agent.multi_agent.state.health_state import HealthState
from app.ai.model.my_model import MyModel, model_fallback_middleware
from app.ai.prompt.bulider_prompt import BuilderPromptYaml
from app.ai.tool.influx_tool import query_range
from app.core.logger import logger
from app.dao.alert_dao import AlertDAO
from app.dao.health_dao import HealthDAO
from app.dao.medication_dao import MedicationDAO
from app.skills.skill_loader import load_skill

# 读取外部提示词（模块顶层加载）
prompt = BuilderPromptYaml.get_prompt("report_node.yaml")

# 报告落盘目录（契约：app/html/reports/）
REPORT_DIR = os.path.join("app", "html", "reports")
# 报告类型 → Skill 名称（怎么写由 SKILL.md 规定）
SKILL_MAP = {"daily": "daily_report", "weekly": "weekly_report"}
# 报告类型 → 统计天数
DAYS_MAP = {"daily": 1, "weekly": 7}
# 体征字段 → 中文名
FIELD_CN = {
    "heart_rate": "心率",
    "blood_pressure_sys": "收缩压",
    "blood_pressure_dia": "舒张压",
    "spo2": "血氧",
    "temperature": "体温",
    "glucose": "血糖",
    "sleep_hours": "睡眠时长",
}


def _stats(points) -> dict:
    """把时序点 [{"time","value"}] 汇总成 计数/均值/最值/趋势（趋势：后半段 vs 前半段，±5%）"""
    values: list[float] = []
    for point in points or []:
        value = point.get("value") if isinstance(point, dict) else point
        try:
            if value not in (None, ""):
                values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not values:
        return {"count": 0, "avg": None, "min": None, "max": None, "trend": "无数据"}

    trend = "平稳"
    if len(values) >= 2:
        mid = len(values) // 2
        front = sum(values[:mid]) / max(mid, 1)
        back = sum(values[mid:]) / max(len(values) - mid, 1)
        if front:
            rate = (back - front) / abs(front)
            trend = "上升" if rate > 0.05 else ("下降" if rate < -0.05 else "平稳")
    return {
        "count": len(values),
        "avg": round(sum(values) / len(values), 1),
        "min": min(values),
        "max": max(values),
        "trend": trend,
    }


def _report_data_text(user_id: str, days: int, knowledge: list[dict]) -> str:
    """把报告需要的全部数据拼成一段纯文本；每项独立 try，失败只写一行提示，不影响其它项"""
    blocks: list[str] = []

    # 1) 体征（逐字段统计）
    vitals: list[str] = []
    for field, cn in FIELD_CN.items():
        try:
            stat = _stats(query_range(user_id, field, days))
            if stat["count"]:
                vitals.append(
                    f"- {cn}：均值 {stat['avg']}，最低 {stat['min']}，最高 {stat['max']}，"
                    f"趋势 {stat['trend']}（{stat['count']} 个数据点）"
                )
            else:
                vitals.append(f"- {cn}：本周期无数据")
        except Exception as e:  # noqa: BLE001
            vitals.append(f"- {cn}：查询失败（{type(e).__name__}）")
    blocks.append(f"【体征（近 {days} 天）】\n" + "\n".join(vitals))

    # 2) 告警
    alerts: list[str] = []
    try:
        for item in AlertDAO.summary_days(user_id, days) or []:
            alerts.append(f"- {item.get('date')}：{item.get('level')} 共 {item.get('count')} 次")
        for item in AlertDAO.list_by_user(user_id, limit=5) or []:
            alerts.append(
                f"- 明细：{item.get('create_time', '')} {item.get('level', '')} {item.get('description', '')}"
            )
    except Exception as e:  # noqa: BLE001
        alerts.append(f"- 查询失败（{type(e).__name__}）")
    blocks.append("【告警】\n" + ("\n".join(alerts) or "- 本周期无告警记录"))

    # 3) 用药
    try:
        plan = MedicationDAO.list_plan(user_id) or []
        today = MedicationDAO.list_today(user_id) or []
        adherence = MedicationDAO.adherence_rate(user_id, days)
        status_count: dict = {}
        for record in today:
            status = str(record.get("status") or "未知")
            status_count[status] = status_count.get(status, 0) + 1
        desc = "；".join(
            f"{p.get('drug_name')}{p.get('dosage') or ''} {p.get('take_time') or ''}" for p in plan[:5]
        )
        blocks.append(
            f"【用药】\n- 用药计划 {len(plan)} 条" + (f"（{desc}）" if desc else "")
            + f"\n- 今日用药记录 {len(today)} 条（状态分布：{status_count or '无'}）"
            + f"\n- 近 {days} 天依从率：{adherence}"
        )
    except Exception as e:  # noqa: BLE001
        blocks.append(f"【用药】\n- 查询失败（{type(e).__name__}）")

    # 4) 健康档案（抬头/个体化建议用）
    try:
        profile = HealthDAO.get_profile(user_id) or {}
        if profile:
            blocks.append(
                "【健康档案】\n" + "\n".join(f"- {key}：{value}" for key, value in profile.items())
            )
    except Exception as e:  # noqa: BLE001
        blocks.append(f"【健康档案】\n- 查询失败（{type(e).__name__}）")

    # 5) RAG 依据（个性化建议必须保留来源：FR-09 溯源）
    if knowledge:
        lines = [
            f"{i}. {item.get('content')}（来源：{item.get('source')}，"
            f"版本：{item.get('version')}，页码：{item.get('page')}）"
            for i, item in enumerate(knowledge, 1)
        ]
        blocks.append("【可引用的知识库依据（引用时必须保留来源）】\n" + "\n".join(lines))

    return "\n\n".join(blocks)


async def report_node(state: HealthState):
    """报告生成节点（流式）：日报 / 周报 —— Skill 规程 + 模型一次生成"""
    user_id = str(state.get("user_id") or "")
    report_type = "daily"
    now = datetime.now()
    try:
        # 1. 报告类型（来自 current_task.args.type，非法值按日报）
        args = (state.get("current_task") or {}).get("args") or {}
        report_type = str(args.get("type") or "daily").lower()
        if report_type not in SKILL_MAP:
            logger.warning(f"[Report] 非法报告类型 {report_type}，按 daily 处理")
            report_type = "daily"
        days = DAYS_MAP[report_type]
        cn_type = "周报" if report_type == "weekly" else "日报"
        logger.info(f"[Report] 生成{cn_type} user_id={user_id} days={days}")

        # 2. 加载 Skill：报告怎么写（章节/语气/溯源）全在 SKILL.md 里
        skill_name = SKILL_MAP[report_type]
        try:
            skill_instruction = load_skill(skill_name, f"用户={user_id}，周期={days}天") or ""
            logger.info(f"[Report] 已加载技能 {skill_name}，指令长度={len(skill_instruction)}")
        except Exception as e:  # noqa: BLE001
            skill_instruction = ""
            logger.error(f"[Report] 技能加载失败，改用默认章节要求：{e}")

        # 3. 取数据（代码负责；模型不查库，避免编造）
        data_text = _report_data_text(user_id, days, state.get("knowledge") or [])

        # 4. 一次 agent 调用：读【数据 + 技能规程】→ 流式生成正文
        title = f"{user_id} 健康{cn_type}（{now:%Y-%m-%d}）"
        user_content = (
            f"报告对象：{user_id}\n"
            f"报告类型：{cn_type}（统计 {days} 天）\n\n"
            f"{data_text}\n\n"
            f"请严格按下面的技能规程，写成给家属看的{cn_type}（Markdown 格式，不要编造数据）：\n\n"
            f"{skill_instruction or '按四章节组织：指标概览 / 异常与告警汇总 / 用药依从性 / 个性化建议。'}"
        )
        report_content = ""
        try:
            agent = create_agent(
                model=MyModel.get_model(),
                system_prompt=prompt,
                middleware=model_fallback_middleware(),
            )
            writer = get_stream_writer()
            fragments: list[str] = []
            async for chunk, _meta in agent.astream(
                {"messages": [HumanMessage(content=user_content)]}, stream_mode="messages"
            ):
                piece = getattr(chunk, "content", None)
                if piece:
                    fragments.append(piece)
                    writer(piece)                      # 流式推给前端
            report_content = "".join(fragments).strip()
            logger.info(f"[Report] 模型生成正文长度={len(report_content)}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[Report] 模型生成失败，改用数据底稿兜底：{e}")

        # 5. 兜底：模型不可用 → 直接用数据底稿成文（保证非空、结构仍在），同样推给前端
        if not report_content:
            report_content = (
                f"# {title}\n\n> 生成时间：{now:%Y-%m-%d %H:%M}（模型暂不可用，以下为数据底稿）\n\n"
                f"{data_text}\n\n---\n\n"
                "本报告由智能健康管家自动生成，仅供健康管理参考，不能替代医生诊断；紧急情况请拨打 120。"
            )
            try:
                get_stream_writer()(report_content)
            except Exception:  # noqa: BLE001
                pass

        # 6. 落盘 Markdown（失败不影响返回内容）
        report_path = ""
        try:
            os.makedirs(REPORT_DIR, exist_ok=True)
            save_path = os.path.abspath(
                os.path.join(REPORT_DIR, f"{user_id}_{report_type}_{now:%Y%m%d}.md")
            )
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(report_content)
            report_path = save_path
            logger.info(f"[Report] 报告已保存：{report_path}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[Report] 报告落盘失败：{e}")

        return {
            "report_type": report_type,
            "report_content": report_content,
            "report_path": report_path,
            "task_results": {
                "report": {
                    "status": "ok",
                    "report_type": report_type,
                    "title": title,
                    "report_path": report_path,
                    "report_content": report_content,
                }
            },
            "health_step": "report",
            "messages": [AIMessage(content=report_content)],
        }
    except Exception as e:
        # 异常：写 error 让 Manager 重试；保留安全默认值
        logger.error(f"[Report] 报告生成失败：{e}")
        return {
            "report_type": report_type,
            "report_content": "",
            "report_path": "",
            "task_results": {"report": {"status": "error", "msg": str(e)}},
            "health_step": "report",
            "messages": [AIMessage(content="报告生成失败，我稍后再试一次。")],
        }


if __name__ == "__main__":
    # 自测：只验证纯函数（统计汇总 / 数据文本渲染），不连数据库与模型
    print("统计：", _stats([{"value": 120}, {"value": 130}, {"value": 150}, {"value": 160}]))
    print("无数据统计：", _stats([]))
    print("技能映射：", SKILL_MAP, "统计天数：", DAYS_MAP)
