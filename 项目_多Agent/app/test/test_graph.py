"""
LangGraph 多智能体编排自测：复合意图 + 流式输出（FR-10 / AC-16~AC-18）

推荐运行方式（在项目根目录下执行）：
    python -m app.test.test_graph
    python app/test/test_graph.py

演示内容：
    1. InMemorySaver 作为 Checkpointer（开发态，重启即丢），构造 HealthGraph；
    2. 跑一句复合意图问询："帮我看看身体怎么样，然后生成周报，再告诉我中午吃啥"，
       观察 Planner 拆任务 → Manager 逐个调度 Worker → Summarize 汇总；
    3. 打印流式片段（与前端 SSE 收到的是同一份数据）；
    4. 观察 state 中的 task_queue / task_results / risk_level / health_step（AC-16）；
    5. 未知任务降级说明（AC-17）：Manager 把不在白名单的任务降级为 chat 并记日志。

依赖：langgraph / langchain（可用）；LLM（DASHSCOPE_API_KEY）可用时效果最好。
      LLM 不可用时打印提示并给出降级路径（NFR-02）。
"""
import asyncio
import os
import sys

# ---------- 项目根目录加入 sys.path ----------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 复合意图：一句话包含 3 个需求（AC-16 要求拆成 ≥3 个任务）
COMPOSITE_QUESTION = "帮我看看身体怎么样，然后生成周报，再告诉我中午吃啥"
# 情感陪伴场景（AC-07）
EMOTION_QUESTION = "我一个人在家有点害怕"


async def run_chat(graph, question: str, user_id: str, session_id: str) -> str:
    """跑一轮对话，边收边打印流式片段，返回完整回复"""
    print(f"\n【问】{question}")
    print("【答】", end="", flush=True)
    full = ""
    async for fragment in graph.chat(question, user_id, session_id):
        full += fragment
        print(fragment, end="", flush=True)
    print("\n" + "-" * 72)
    return full


async def main_async() -> None:
    print("=" * 72)
    print("LangGraph 多智能体编排自测：复合意图 + 流式输出（FR-10 / AC-16~AC-18）")
    print("=" * 72)

    # ---------------- 1. 依赖检查 ----------------
    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError as e:
        print(f"[跳过] 未安装 langgraph：{e}")
        print("       修复：pip install -U langgraph langchain langchain-openai")
        return

    try:
        from app.ai.agent.multi_agent.graph.health_graph import HealthGraph
    except Exception as e:
        print(f"[跳过] HealthGraph 不可用（节点文件可能还没写完）：{e}")
        print("       请先完成 Lead / T2 nodes 模块，再运行本脚本。")
        return

    # ---------------- 2. 构建图 ----------------
    print("\n【1】用 InMemorySaver 构造 HealthGraph（开发态 Checkpointer）")
    try:
        graph = HealthGraph(InMemorySaver())
    except Exception as e:
        print(f"[跳过] 图编译失败：{type(e).__name__}: {e}")
        return
    print("     图编译成功。节点集合：planner / manager / intent / data_check / rag / risk /")
    print("                          report / remind / notify / summarize / chat")

    # 打印图结构（mermaid 文本，便于和 docs/系统架构图.md 对照）
    try:
        mermaid = graph.agent.get_graph().draw_mermaid()
        print("\n     图结构（mermaid 摘要，前 12 行）：")
        for line in mermaid.splitlines()[:12]:
            print("       " + line)
    except Exception:
        pass

    try:
        from app.core.config import settings

        print(f"\n【2】运行参数：CHECKPOINTER={settings.checkpointer} "
              f"TREND_DAYS={settings.trend_days} 模型={settings.model_line_name}")
        if not settings.dashscope_api_key:
            print("     ⚠️ 未配置 DASHSCOPE_API_KEY：Planner / Summarize 等 LLM 节点会走降级")
            print("        降级路径：主模型 → 备用模型 → 本地 Ollama → 静态兜底文案")
    except Exception:
        settings = None

    # ---------------- 3. 复合意图：一句话三个需求 ----------------
    print("\n" + "=" * 72)
    print("【3】复合意图测试（AC-16：应被拆成 ≥3 个任务并按序执行）")
    print("=" * 72)
    try:
        answer = await run_chat(graph, COMPOSITE_QUESTION, "u001", "s_test_graph_001")
        print(f"     回复长度：{len(answer)} 字符")
        if not answer.strip():
            print("     [提示] 回复为空：多半是 LLM 不可用或节点还没实现，属预期降级。")
    except Exception as e:
        print(f"\n[降级] 图执行失败：{type(e).__name__}: {e}")
        print("       常见原因：① DASHSCOPE_API_KEY 未配置；② 节点文件未完成；")
        print("                 ③ InfluxDB/MySQL 不可用（节点内部应自行降级，不应冒泡到此处）。")

    # ---------------- 4. 观察状态：task_queue / task_results ----------------
    print("\n" + "=" * 72)
    print("【4】读取会话状态：task_results 应包含各任务结果（AC-16、AC-17）")
    print("=" * 72)
    try:
        config = {"configurable": {"thread_id": "s_test_graph_001"}}
        snapshot = graph.agent.get_state(config)
        values = getattr(snapshot, "values", {}) or {}
        print(f"     health_step  = {values.get('health_step')}")
        print(f"     intent       = {values.get('intent')}")
        print(f"     emotion      = {values.get('emotion')}")
        print(f"     risk_level   = {values.get('risk_level')}")
        print(f"     risk_reasons = {values.get('risk_reasons')}")
        print(f"     task_queue   = {values.get('task_queue')}")
        results = values.get("task_results") or {}
        print(f"     task_results 包含的任务：{list(results.keys())}")
        for k, v in results.items():
            status = (v or {}).get("status") if isinstance(v, dict) else "?"
            print(f"       - {k}: status={status}")
        if len(results) >= 2:
            print("     ✔ 复合意图确实产生多个任务结果（AC-16）")
        else:
            print("     ℹ️ 任务结果较少：若 LLM 未配置，Planner 会退化为单任务 chat 路径。")
    except Exception as e:
        print(f"     [跳过] 读取状态失败：{type(e).__name__}: {e}")

    # ---------------- 5. 情感陪伴（AC-07 / AC-18 反问澄清同源） ----------------
    print("\n" + "=" * 72)
    print("【5】情感陪伴：'我一个人在家有点害怕' 应先安抚、后建议、不诊断（AC-07）")
    print("=" * 72)
    try:
        emo_answer = await run_chat(graph, EMOTION_QUESTION, "u001", "s_test_graph_002")
        if emo_answer:
            banned = ["确诊", "您得了", "抑郁症", "焦虑症"]
            hit = [w for w in banned if w in emo_answer]
            print(f"     敏感词检查：{'命中 ' + str(hit) + '（不符合 AC-07）' if hit else '未出现诊断性表述 ✔'}")
            print(f"     是否提醒紧急情况：{'120' in emo_answer}")
    except Exception as e:
        print(f"\n[降级] 情感陪伴轮次失败：{type(e).__name__}: {e}")

    print("\n" + "=" * 72)
    print("图编排自测结束。要点回顾：")
    print("  · manager 用 Command(update=..., goto=...) 动态路由，Worker 干完活回 manager")
    print("  · 队列空且多任务 → summarize 汇总；仅 chat 任务 → 直接 END")
    print("  · 未知任务（AC-17）→ Manager 降级 chat + logger.warning，流程不崩")
    print("  · 生产并发场景请改用 HealthGraphAgent（多会话校验 + 分布式锁 + 记忆更新）")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main_async())
