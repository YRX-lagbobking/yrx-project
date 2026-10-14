"""
健康检查与降级模式自测（FR-15 / AC-26）

推荐运行方式（在项目根目录下执行）：
    python -m app.test.test_health_check
    python app/test/test_health_check.py

演示内容：
    1. check_all()   —— 真实探测 6 项依赖：MySQL / PostgreSQL / Redis / InfluxDB / Milvus / MCP Server；
    2. detect_mode(status) —— 由探测结果推导降级模式：full / partial / readonly；
    3. 打印前端该怎么做：/health 返回 {"status","mode","detail"}，
       前端按 mode 关闭不可用功能并显示顶部横幅；
    4. AC-26 验证方法：停掉 Milvus 与 MCP Server 后 /health 应返回 degraded/partial，
       聊天功能仍可用（模型与 MySQL 还在）。

依赖：无强制依赖（所有探测都在函数内部 try/except，探测失败即视为不可用）。
"""
import os
import sys

# ---------- 项目根目录加入 sys.path ----------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 各依赖不可用时的用户可见影响（便于演示"降级不崩"）
IMPACT = {
    "mysql": "用户/档案/用药/告警/知识文档读写不可用 → 只读模式，知识检索降级为静态建议",
    "postgres": "Checkpointer 降级为 InMemorySaver（重启丢记忆）；L2 摘要记忆不可用",
    "redis": "L1 窗口记忆 / L4 画像 / 会话锁不可用 → 多轮上下文变弱，告警落本地队列",
    "influxdb": "生命体征时序不可读 → data_check 标记 data_missing，风险检测走简化版 detect_latest",
    "milvus": "向量检索不可用 → RAG 自动降级为关键词检索（MySQL）→ 再降级静态建议",
    "mcp_server": "远程工具不可用 → 本地工具实现 → 缓存结果 → 待处理队列（五级兜底）",
}

# 各项依赖对应的服务名（故障注入演示时用）
SERVICE = {
    "mysql": "MySQL 8",
    "postgres": "PostgreSQL 16（Checkpointer + 摘要记忆）",
    "redis": "Redis 7（窗口记忆 / 画像 / 会话锁 / Celery broker）",
    "influxdb": "InfluxDB 3 Core（vital_signs 时序）",
    "milvus": "Milvus 2.4（health_knowledge / long_memory）",
    "mcp_server": "MCP Server（端口 9000，KeyVerifier 鉴权）",
}


def main() -> None:
    print("=" * 72)
    print("健康检查与降级模式自测（FR-15 / AC-26）")
    print("=" * 72)

    try:
        from app.core.health_check import check_all, detect_mode
    except Exception as e:
        print(f"[跳过] app/core/health_check.py 不可用：{e}")
        print("       请先完成 core-dao 模块（契约第 6 节），再运行本脚本。")
        return

    # ---------------- 1. 逐项探测 ----------------
    print("\n【1】check_all()：真实探测 6 项依赖（每项独立 try/except，不互相影响）")
    try:
        status = check_all()
    except Exception as e:
        print(f"     [降级] check_all 本身抛异常（不符合契约，应自行吞掉）："
              f"{type(e).__name__}: {e}")
        return

    print(f"\n     {'依赖':<12}{'状态':<8}说明 / 服务名")
    print("     " + "-" * 66)
    ok_count = 0
    for key in ("mysql", "postgres", "redis", "influxdb", "milvus", "mcp_server"):
        alive = bool(status.get(key))
        ok_count += 1 if alive else 0
        mark = "✔ 可用" if alive else "✘ 不可用"
        print(f"     {key:<12}{mark:<8}{SERVICE.get(key, '')}")

    if len(status) != 6:
        print(f"     ⚠️ 返回键数为 {len(status)}，契约要求正好 6 项："
              f"mysql/postgres/redis/influxdb/milvus/mcp_server")

    # ---------------- 2. 降级模式推导 ----------------
    print("\n【2】detect_mode(status)：推导降级模式")
    try:
        mode = detect_mode(status)
    except Exception as e:
        print(f"     [降级] detect_mode 调用失败：{type(e).__name__}: {e}")
        return

    mode_desc = {
        "full": "全功能：所有依赖在线，前端不做任何功能裁剪",
        "partial": "部分功能：核心链路（模型 + MySQL + Redis）可用，向量/远程工具降级",
        "readonly": "只读模式：写库依赖不可用，仅提供查询与静态页面",
    }
    print(f"     mode = {mode}")
    print(f"     含义：{mode_desc.get(mode, '（未知模式，请检查实现）')}")
    print(f"     在线依赖数：{ok_count}/6")

    # ---------------- 3. 不可用项的影响与对策 ----------------
    down = [k for k in SERVICE if not status.get(k)]
    print(f"\n【3】当前不可用依赖（{len(down)} 项）及影响：")
    if not down:
        print("     全部在线，无需降级。可以试着停掉 Milvus 或 MCP Server 再跑一次本脚本，")
        print("     验证 AC-26：/health 应返回 degraded/partial，聊天功能仍可用。")
    for key in down:
        print(f"     · {key}：{IMPACT.get(key, '')}")

    # ---------------- 4. /health 接口形态 ----------------
    print("\n【4】GET /health 的返回形态（前端据此显示横幅）")
    status_word = "healthy" if all(status.values()) else ("critical" if ok_count <= 1 else "degraded")
    print("     {")
    print(f'       "status": "{status_word}",')
    print(f'       "mode":   "{mode}",')
    print('       "detail": {')
    for i, (k, v) in enumerate(status.items()):
        comma = "," if i < len(status) - 1 else ""
        print(f'         "{k}": {str(bool(v)).lower()}{comma}')
    print("       }")
    print("     }")

    print("\n     前端处理建议：")
    print("       full     → 正常；")
    print("       partial  → 顶部黄色横幅\"部分功能降级\"，隐藏依赖向量库的\"依据\"展开按钮；")
    print("       readonly → 顶部橙色横幅，禁用发送/登记类操作，只保留查询与静态页面。")

    print("\n" + "=" * 72)
    print("健康检查自测结束。六层降级总览：")
    print("  模型：主 → 备用 → 本地 Ollama → 静态兜底文案")
    print("  工具：MCP → 本地实现 → 缓存 → 待处理队列")
    print("  节点：完整 → 简化 → 返回\"未知\"")
    print("  RAG ：向量+重排 → 关键词 → 静态建议")
    print("  服务：全功能 → 部分功能 → 只读 → 静态页面")
    print("  告警：短信 → 邮件 → 备用网关 → Celery 重试 → 本地队列")
    print("=" * 72)


if __name__ == "__main__":
    main()
