"""
健康报告生成工具（MCP 工具：generate_report）

职责：
    为某个老人生成日报 / 周报正文（Markdown 文本），内容包含：
        1. 指标概览（均值 / 最值 / 趋势方向）
        2. 异常与告警汇总（来自 AlertDAO）
        3. 用药依从性（MedicationDAO.adherence_rate）
        4. 基于知识库的个性化建议（rag_tool 检索，可选）
        5. 固定免责声明（R-08：健康建议不得被当作医疗诊断）

数据来源（全部同步 DAO + 工具函数，任一步失败只降级、不中断）：
    vitals_tool.get_vitals、AlertDAO.list_by_user、MedicationDAO.adherence_rate、
    knowledge_tool.search_knowledge

返回：Markdown 字符串（永不抛异常；无数据也会返回一份带说明的报告）。
"""
from datetime import datetime

from app.core.config import settings
from app.core.logger import logger

# 固定免责声明（R-08 伦理与合规要求）
DISCLAIMER = (
    "\n---\n"
    "> 免责声明：本报告由智能健康管家依据居家监测数据自动生成，"
    "仅为健康提示与生活方式建议，**不构成医疗诊断或处方**。"
    "如有不适请及时就医，紧急情况请立即拨打 120。\n"
)

# 指标中文名（报告可读性）
FIELD_LABELS = {
    "heart_rate": "心率(次/分)",
    "blood_pressure_sys": "收缩压(mmHg)",
    "blood_pressure_dia": "舒张压(mmHg)",
    "spo2": "血氧(%)",
    "temperature": "体温(℃)",
    "glucose": "血糖(mmol/L)",
    "sleep_hours": "睡眠(小时)",
}


def _trend_text(stats: dict) -> str:
    """由统计摘要粗略判断趋势方向（最新值 vs 均值偏离）"""
    latest = stats.get("latest")
    avg = stats.get("avg")
    if not isinstance(latest, (int, float)) or not isinstance(avg, (int, float)) or not avg:
        return "数据不足"
    deviation = (latest - avg) / avg
    threshold = float(settings.trend_deviation or 0.3)
    if deviation > threshold:
        return f"明显高于均值（偏离 {deviation:.0%}）"
    if deviation < -threshold:
        return f"明显低于均值（偏离 {deviation:.0%}）"
    return f"基本平稳（偏离 {deviation:+.0%}）"


def generate_report(user_id: str, type: str = "daily") -> str:
    """
    生成健康报告正文

    参数：
        user_id 老人用户编号
        type    报告类型：daily（日报）/ weekly（周报）
    返回：
        Markdown 格式报告字符串
    """
    user_id = (user_id or "").strip()
    report_type = (type or "daily").strip().lower()
    is_weekly = report_type in ("weekly", "week", "周报")
    days = 7 if is_weekly else 1
    title = "健康周报" if is_weekly else "健康日报"

    lines: list[str] = [
        f"# {title}",
        "",
        f"- 用户：{user_id or '（未知用户）'}",
        f"- 统计区间：最近 {days} 天",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if not user_id:
        lines.append("未提供用户编号，无法生成报告数据。")
        lines.append(DISCLAIMER)
        return "\n".join(lines)

    # ---------------- 1. 指标概览 ----------------
    lines.append("## 一、指标概览")
    lines.append("")
    vitals: dict = {}
    try:
        from mcp_server.tools.vitals_tool import get_vitals

        vitals = get_vitals(user_id, days=days)
        field_stats = vitals.get("fields") or {}
        if field_stats:
            lines.append("| 指标 | 最低 | 平均 | 最高 | 最新 | 趋势 |")
            lines.append("| :--- | ---: | ---: | ---: | ---: | :--- |")
            for field, stats in field_stats.items():
                lines.append(
                    f"| {FIELD_LABELS.get(field, field)} | {stats.get('min')} | {stats.get('avg')} | "
                    f"{stats.get('max')} | {stats.get('latest')} | {_trend_text(stats)} |"
                )
        else:
            lines.append(f"最近 {days} 天暂无体征数据（请检查设备或数据采集服务是否运行）。")
    except Exception as e:
        logger.error(f"generate_report 生成指标概览失败：{e}")
        lines.append(f"指标概览生成失败：{e}")
    lines.append("")

    # ---------------- 2. 异常与告警汇总 ----------------
    lines.append("## 二、异常与告警")
    lines.append("")
    try:
        abnormal = vitals.get("abnormal") or []
        if abnormal:
            for item in abnormal:
                lines.append(f"- ⚠️ {item.get('message', '')}")
        else:
            lines.append("- 最新体征未命中阈值告警。")
    except Exception as e:
        logger.error(f"generate_report 生成阈值提示失败：{e}")

    try:
        from app.dao.alert_dao import AlertDAO

        alerts = AlertDAO.list_by_user(user_id, limit=10) or []
        if alerts:
            lines.append("")
            lines.append("近期告警记录：")
            lines.append("")
            lines.append("| 时间 | 等级 | 类型 | 描述 |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for alert in alerts:
                lines.append(
                    f"| {alert.get('create_time') or alert.get('time') or '-'} | "
                    f"{alert.get('level', '-')} | {alert.get('alert_type', '-')} | "
                    f"{alert.get('description', '-')} |"
                )
        else:
            lines.append("- 暂无告警记录。")
    except Exception as e:
        logger.warning(f"generate_report 查询告警记录失败（降级）：{e}")
    lines.append("")

    # ---------------- 3. 用药依从性 ----------------
    lines.append("## 三、用药依从性")
    lines.append("")
    try:
        from app.dao.medication_dao import MedicationDAO

        rate = MedicationDAO.adherence_rate(user_id, days=days)
        if isinstance(rate, (int, float)):
            lines.append(f"- 最近 {days} 天服药依从率：{rate:.0%}")
            if rate < 0.8:
                lines.append("- ⚠️ 依从率偏低，建议家属提醒老人按时服药，并核对是否有漏服登记遗漏。")
        else:
            lines.append("- 暂无用药记录。")
    except Exception as e:
        logger.warning(f"generate_report 查询用药依从率失败（降级）：{e}")
        lines.append("- 用药数据暂不可用。")
    lines.append("")

    # ---------------- 4. 个性化建议（知识库溯源） ----------------
    lines.append("## 四、个性化建议")
    lines.append("")
    advice_added = False
    try:
        from mcp_server.tools.knowledge_tool import search_knowledge

        # 用异常项拼查询词，让建议更贴合本人情况
        abnormal_fields = [item.get("field", "") for item in (vitals.get("abnormal") or [])]
        query_text = " ".join([FIELD_LABELS.get(f, f) for f in abnormal_fields]) or "老年人慢病日常管理"
        pieces = search_knowledge(query_text, top_k=3)
        if pieces:
            for index, piece in enumerate(pieces, start=1):
                lines.append(
                    f"{index}. {piece.get('content', '')}"
                    f"（来源：{piece.get('source', '未知')} {piece.get('version', '')} 第 {piece.get('page', '-')} 页）"
                )
            advice_added = True
    except Exception as e:
        logger.warning(f"generate_report 检索知识库失败（降级）：{e}")

    if not advice_added:
        lines.append("1. 保持规律作息与低盐低脂饮食，适度活动，注意防跌倒。")
        lines.append("2. 按时服药并做好记录，定期监测血压、血糖、心率。")
        lines.append("3. 如出现持续头晕、胸闷、呼吸困难等不适，请及时就医。")
    lines.append("")

    lines.append(DISCLAIMER)
    content = "\n".join(lines)
    logger.info(f"generate_report 完成：user_id={user_id} type={report_type} 长度={len(content)}")
    return content


if __name__ == "__main__":
    # 自测：中间件全不可用时也应返回一份带说明的报告（不抛异常）
    print(generate_report("elder_demo", "daily"))
    print("=" * 60)
    print(generate_report("", "weekly")[:300])
