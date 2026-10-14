"""
报告路由（日报 / 周报生成与 Word 导出）

职责：
    1. GET /report/daily  ：生成日报（指标概览 / 异常汇总 / 用药依从性 / 建议）；
    2. GET /report/weekly ：生成周报（同上，窗口 7 天）；
    3. GET /report/download：把报告写成 .docx 并下载（python-docx）。

设计说明：
    1. 报告正文优先复用 AI 层能力（MCP 工具 mcp_server.tools.report_tool.generate_report），
       不可用时降级为"DAO + InfluxDB 时序"拼装的基础报告，保证接口永远有结果（NFR-02）；
    2. DAO 与 MCP 工具都是同步函数，在 async 路由里统一用 asyncio.to_thread 包装
       （生产应改为异步连接池；教学规模下这是最小改动且不阻塞事件循环）；
    3. 数据权限：user_id 必须通过 can_access_user 校验（老人只能查自己，家属只能查绑定老人）。
"""
import asyncio
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from starlette.responses import JSONResponse

from app.core.logger import logger
from app.core.security import TokenData, can_access_user, get_current_user

report_router = APIRouter()

# 报告落盘目录（相对项目根目录，与契约中的 ./app/data/reports/ 一致）
REPORT_DIR = "./app/data/reports"
# 报告小节标题（AC-13 要求含"指标概览/异常汇总/用药依从性/建议"四部分）
SECTION_TITLES = ("指标概览", "异常汇总", "用药依从性", "个性化建议")
# 生命体征字段 → 中文名（时序概览用）
VITAL_NAMES = [
    ("heart_rate", "心率(次/分)"),
    ("blood_pressure_sys", "收缩压(mmHg)"),
    ("blood_pressure_dia", "舒张压(mmHg)"),
    ("spo2", "血氧(%)"),
    ("temperature", "体温(℃)"),
    ("glucose", "血糖(mmol/L)"),
    ("sleep_hours", "睡眠(小时)"),
]


async def _check_access(current: TokenData, user_id: str) -> bool:
    """
    数据权限校验

    家属只能访问已绑定老人 → 用 UserDAO.list_bound_elders 取绑定列表，再交给统一的 can_access_user 判定。
    DAO 是同步函数（pymysql），放线程池执行，避免阻塞事件循环。
    """
    try:
        bound = []
        if current.role == "family":
            from app.dao.user_dao import UserDAO

            bound = await asyncio.to_thread(UserDAO.list_bound_elders, current.user_id)
        return can_access_user(current, str(user_id), [str(x) for x in (bound or [])])
    except Exception as e:
        logger.error(f"数据权限校验异常，按拒绝处理：{e}")
        return False


def _stat(values: list) -> dict:
    """对一段时序值做基础统计（条数/均值/最小/最大），异常值忽略"""
    nums = []
    for v in values:
        try:
            if v is not None:
                nums.append(float(v))
        except (TypeError, ValueError):
            continue
    if not nums:
        return {"count": 0}
    return {
        "count": len(nums),
        "avg": round(sum(nums) / len(nums), 2),
        "min": round(min(nums), 2),
        "max": round(max(nums), 2),
    }


def _build_basic_report(user_id: str, report_type: str, days: int) -> dict:
    """
    降级路径：不依赖 MCP，用 DAO + InfluxDB 直接拼一份基础报告（同步函数，调用方 to_thread 包装）
    """
    today = datetime.now().strftime("%Y-%m-%d")
    label = "周报" if report_type == "weekly" else "日报"
    sections = []

    # ---- 1. 指标概览：来自 InfluxDB 时序 ----
    overview_lines = []
    try:
        from app.ai.tool.influx_tool import query_latest, query_range

        latest = query_latest(user_id) or {}
        for field, name in VITAL_NAMES:
            points = query_range(user_id, field, days) or []
            stat = _stat([p.get("value") for p in points if isinstance(p, dict)])
            current = latest.get(field)
            if stat["count"] == 0 and current is None:
                continue
            if stat["count"] == 0:
                overview_lines.append(f"{name}：当前 {current}（窗口内无历史数据）")
            else:
                overview_lines.append(
                    f"{name}：当前 {current if current is not None else '—'}，"
                    f"近 {days} 天均值 {stat['avg']}，最低 {stat['min']}，最高 {stat['max']}（{stat['count']} 条）"
                )
    except Exception as e:
        logger.error(f"指标概览生成失败（降级）：{e}")
    if not overview_lines:
        overview_lines.append("窗口内暂无生命体征数据，建议检查设备采集或数据模拟器是否启动。")
    sections.append({"title": SECTION_TITLES[0], "content": "\n".join(overview_lines)})

    # ---- 2. 异常汇总：来自告警记录 ----
    alert_lines = []
    try:
        from app.dao.alert_dao import AlertDAO

        alerts = AlertDAO.list_by_user(user_id, limit=20) or []
        if alerts:
            for a in alerts:
                alert_lines.append(
                    f"[{a.get('level', '—')}] {a.get('alert_type', '')} "
                    f"{a.get('description', '')}（{a.get('create_time', '')}，状态：{a.get('status', '待确认')}）"
                )
        else:
            alert_lines.append(f"近 {days} 天没有产生新的告警记录，整体平稳。")
    except Exception as e:
        logger.error(f"异常汇总生成失败（降级）：{e}")
        alert_lines.append("告警记录读取失败，请稍后重试。")
    sections.append({"title": SECTION_TITLES[1], "content": "\n".join(alert_lines)})

    # ---- 3. 用药依从性：来自用药记录 ----
    med_lines = []
    try:
        from app.dao.medication_dao import MedicationDAO

        adherence = MedicationDAO.adherence_rate(user_id, days)
        plans = MedicationDAO.list_plan(user_id) or []
        med_lines.append(f"近 {days} 天用药依从率：{round(float(adherence or 0) * 100, 1)}%")
        if plans:
            for p in plans[:10]:
                med_lines.append(
                    f"{p.get('drug_name', '')} {p.get('dosage', '')} "
                    f"{p.get('frequency', '')} 服用时间 {p.get('take_time', '')}"
                )
        else:
            med_lines.append("当前没有维护用药计划。")
    except Exception as e:
        logger.error(f"用药依从性生成失败（降级）：{e}")
        med_lines.append("用药数据读取失败，请稍后重试。")
    sections.append({"title": SECTION_TITLES[2], "content": "\n".join(med_lines)})

    # ---- 4. 个性化建议：RAG（失败自动降级为静态建议）----
    try:
        from app.core.fallback import rag_search_with_fallback

        advice = rag_search_with_fallback("老年人血压心率日常管理注意事项", 3)
    except Exception as e:
        logger.error(f"建议检索失败（降级为静态文案）：{e}")
        advice = "请保持规律作息、清淡饮食与适度活动；指标异常请咨询专业医生，紧急情况拨打 120。"
    sections.append({"title": SECTION_TITLES[3], "content": advice + "\n\n（以上内容为健康提示，不构成医疗诊断。）"})

    # 汇总摘要
    summary = "；".join(
        [
            f"报告类型：{label}",
            f"统计窗口：近 {days} 天",
            f"异常提示：{len(alert_lines)} 条记录",
            f"生成时间：{today}",
        ]
    )
    content = "\n\n".join(f"【{s['title']}】\n{s['content']}" for s in sections)
    return {
        "title": f"健康报告（{label}）· 用户 {user_id}",
        "summary": summary,
        "content": content,
        "sections": sections,
        "report_type": report_type,
        "user_id": user_id,
    }


def _parse_mcp_report(text: str, user_id: str, report_type: str, days: int) -> dict:
    """把 MCP 返回的报告正文包装成统一结构（MCP 只返回字符串正文）"""
    label = "周报" if report_type == "weekly" else "日报"
    return {
        "title": f"健康报告（{label}）· 用户 {user_id}",
        "summary": f"报告类型：{label}；统计窗口：近 {days} 天；生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "content": text,
        "sections": [{"title": "报告正文", "content": text}],
        "report_type": report_type,
        "user_id": user_id,
    }


async def _generate_report_data(user_id: str, report_type: str, days: int) -> dict:
    """
    生成报告数据（优先 MCP 工具，失败降级本地拼装）

    返回统一结构：{"title", "summary", "content", "sections", "report_type", "user_id"}
    """
    # 1. 优先复用 AI 层能力：MCP 报告工具（同步函数 → to_thread）
    try:
        from mcp_server.tools.report_tool import generate_report

        text = await asyncio.to_thread(generate_report, user_id, report_type)
        if text and str(text).strip():
            logger.info(f"MCP 报告工具生成成功：user_id={user_id}, type={report_type}")
            return _parse_mcp_report(str(text), user_id, report_type, days)
        logger.warning(f"MCP 报告工具返回空内容，降级本地拼装：user_id={user_id}")
    except Exception as e:
        logger.warning(f"MCP 报告工具不可用，降级本地拼装：{e}")

    # 2. 降级：DAO + InfluxDB 拼装（同样是同步逻辑 → to_thread）
    try:
        return await asyncio.to_thread(_build_basic_report, user_id, report_type, days)
    except Exception as e:
        logger.error(f"基础报告生成失败：{e}")
        return {
            "title": f"健康报告（{'周报' if report_type == 'weekly' else '日报'}）· 用户 {user_id}",
            "summary": "报告生成失败",
            "content": "报告生成失败，请稍后重试；如需了解身体状况，请咨询专业医生。",
            "sections": [],
            "report_type": report_type,
            "user_id": user_id,
        }


async def _report(user_id: str, report_type: str, days: int, user: TokenData):
    """日报 / 周报共用处理逻辑（鉴权 + 生成 + 统一返回风格）"""
    # 数据权限：老人只能查自己，家属只能查绑定老人，管理员不限
    if not await _check_access(user, user_id):
        logger.warning(f"报告越权访问被拒绝：current={user.user_id}, target={user_id}")
        return JSONResponse(status_code=403, content={"code": 403, "msg": "无权访问该用户数据", "data": None})

    data = await _generate_report_data(str(user_id), report_type, days)
    # 契约第 18 节：返回 {"code":200,"data":{"title","content","summary"}}
    return {
        "code": 200,
        "msg": "生成成功",
        "data": {
            "title": data["title"],
            "content": data["content"],
            "summary": data["summary"],
        },
    }


@report_router.get("/report/daily")
async def report_daily(
    user_id: str = Query(..., description="目标用户 ID"),
    days: int = Query(default=1, ge=1, le=90, description="统计天数，默认 1 天"),
    user: TokenData = Depends(get_current_user),
):
    """日报生成"""
    logger.info(f"日报请求：user_id={user_id}, days={days}")
    return await _report(user_id, "daily", days, user)


@report_router.get("/report/weekly")
async def report_weekly(
    user_id: str = Query(..., description="目标用户 ID"),
    days: int = Query(default=7, ge=1, le=90, description="统计天数，默认 7 天"),
    user: TokenData = Depends(get_current_user),
):
    """周报生成"""
    logger.info(f"周报请求：user_id={user_id}, days={days}")
    return await _report(user_id, "weekly", days, user)


def _write_docx(report: dict, file_path: str) -> None:
    """
    把报告写成 Word 文档（同步函数，调用方 to_thread 包装）

    python-docx 延迟导入：未安装时抛 ImportError，由调用方返回 500 + 明确提示。
    """
    from docx import Document  # 延迟导入可选依赖

    doc = Document()
    doc.add_heading(report.get("title") or "健康报告", level=0)
    doc.add_paragraph(f"摘要：{report.get('summary') or '—'}")

    sections = report.get("sections") or []
    if sections:
        for sec in sections:
            doc.add_heading(sec.get("title") or "小节", level=1)
            # 正文按换行分段写入，保证 Word 里可读
            for line in str(sec.get("content") or "").split("\n"):
                doc.add_paragraph(line)
    else:
        # 没有结构化小节时，退化为整体正文
        for line in str(report.get("content") or "").split("\n"):
            doc.add_paragraph(line)

    doc.add_paragraph("本报告由智能健康管家自动生成，仅为健康提示，不构成医疗诊断；紧急情况请拨打 120。")
    doc.save(file_path)


@report_router.get("/report/download")
async def report_download(
    request: Request,
    user_id: str = Query(..., description="目标用户 ID"),
    type: str = Query(default="weekly", description="报告类型：daily / weekly"),
    user: TokenData = Depends(get_current_user),
):
    """报告导出 Word（.docx），文件名形如 健康报告_周报_{user_id}.docx"""
    # 数据权限：user_id 必须通过 can_access_user 校验（家属只能查绑定老人）
    if not await _check_access(user, user_id):
        logger.warning(f"报告下载越权被拒绝：current={user.user_id}, target={user_id}")
        return JSONResponse(status_code=403, content={"code": 403, "msg": "无权访问该用户数据", "data": None})

    report_type = "weekly" if str(type).lower() == "weekly" else "daily"
    days = 7 if report_type == "weekly" else 1
    label = "周报" if report_type == "weekly" else "日报"
    logger.info(f"报告下载请求：user_id={user_id}, type={report_type}")

    # 1. 先生成报告数据（MCP 优先，失败降级）
    report = await _generate_report_data(str(user_id), report_type, days)

    # 2. 落盘目录（不存在则创建，并做路径穿越校验）
    try:
        base_dir = os.path.abspath(REPORT_DIR)
        os.makedirs(base_dir, exist_ok=True)
        safe_user_id = "".join(ch for ch in str(user_id) if ch.isalnum() or ch in "-_") or "unknown"
        file_path = os.path.abspath(os.path.join(base_dir, f"健康报告_{label}_{safe_user_id}.docx"))
        if not file_path.startswith(base_dir + os.sep):
            logger.error(f"报告路径非法：{file_path}")
            return JSONResponse(status_code=400, content={"code": 400, "msg": "用户 ID 非法", "data": None})
    except Exception as e:
        logger.error(f"报告目录创建失败：{e}")
        return JSONResponse(status_code=500, content={"code": 500, "msg": "报告目录不可写", "data": None})

    # 3. 写 Word（python-docx 未安装时明确提示）
    try:
        await asyncio.to_thread(_write_docx, report, file_path)
    except ImportError:
        logger.error("python-docx 未安装，无法导出 Word")
        return JSONResponse(
            status_code=500,
            content={"code": 500, "msg": "python-docx 未安装，请先执行 pip install python-docx", "data": None},
        )
    except Exception as e:
        logger.error(f"Word 报告生成失败：{e}")
        return JSONResponse(status_code=500, content={"code": 500, "msg": "报告导出失败", "data": None})

    # 4. 返回文件流（中文文件名用 filename* 交给浏览器解码）
    file_name = f"健康报告_{label}_{user_id}.docx"
    return FileResponse(
        path=file_path,
        filename=file_name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ---------------- 调用示例 ----------------
# 1) 日报
#    curl "http://localhost:8000/report/daily?user_id=u001&days=1" -H "Authorization: Bearer <token>"
# 2) 周报
#    curl "http://localhost:8000/report/weekly?user_id=u001&days=7" -H "Authorization: Bearer <token>"
# 3) 下载 Word
#    curl -OJ "http://localhost:8000/report/download?user_id=u001&type=weekly" -H "Authorization: Bearer <token>"
