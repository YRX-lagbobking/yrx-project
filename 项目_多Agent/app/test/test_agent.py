"""
create_agent 自测：工具调用 + 异步流式输出（FR-01~FR-03）

推荐运行方式（在项目根目录下执行）：
    python -m app.test.test_agent
    python app/test/test_agent.py

演示内容：
    1. MyModel.get_model() 取云端主模型（流式）；
    2. create_agent(model=..., tools=[send_email_tool], system_prompt=...) 组装智能体；
    3. agent.astream(..., stream_mode="messages") 逐段打印模型的输出（打字机效果的数据源）；
    4. 让模型调用 send_email_tool 发一封测试邮件，观察"工具调用 → 工具返回 → 总结"的完整链路。

依赖：langchain / langchain-openai（可用），以及有效的 DASHSCOPE_API_KEY。
      模型不可用时打印提示并正常退出（NFR-02 六层降级中的"模型降级"）。
"""
import asyncio
import os
import sys

# ---------- 项目根目录加入 sys.path ----------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 智能体的中文系统提示词（与 app/ai/prompt/chat_node.yaml 的设计思路一致）
SYSTEM_PROMPT = (
    "你是面向独居老人的健康管家助手。请遵守以下规则：\n"
    "1. 用温和、口语化的中文回答，句子要短，不要用专业术语堆砌；\n"
    "2. 只做健康提示，不做医疗诊断；涉及紧急症状时提醒拨打 120；\n"
    "3. 需要发邮件时调用 send_email_tool 工具，不要凭空说\"已发送\"；\n"
    "4. 回答末尾附一句免责声明。"
)


async def run_agent(question: str) -> None:
    """构建智能体并异步流式输出回答"""
    from langchain.agents import create_agent

    from app.ai.model.my_model import MyModel
    from app.ai.tool.send_email_tool import send_email_tool

    agent = create_agent(
        model=MyModel.get_model(),
        tools=[send_email_tool],
        system_prompt=SYSTEM_PROMPT,
    )

    print(f"\n【问】{question}")
    print("【答】", end="", flush=True)

    # stream_mode="messages" 会逐条产出 (message_chunk, metadata)
    async for chunk, meta in agent.astream(
        {"messages": [{"role": "user", "content": question}]},
        stream_mode="messages",
    ):
        # 工具调用片段与工具返回片段不打印正文，只在控制台留个痕迹
        node = (meta or {}).get("langgraph_node", "")
        if getattr(chunk, "tool_call_chunks", None):
            print(f"\n     [工具调用] {getattr(chunk, 'name', '') or ''} "
                  f"{''.join(c.get('args', '') for c in chunk.tool_call_chunks)}")
            continue
        if chunk.__class__.__name__ == "ToolMessage":
            print(f"\n     [工具返回] {str(chunk.content)[:120]}")
            continue
        text = getattr(chunk, "content", "")
        if text:
            print(text, end="", flush=True)

    print("\n" + "-" * 72)


async def main_async() -> None:
    print("=" * 72)
    print("create_agent 自测：工具调用 + 异步流式输出（FR-01~FR-03）")
    print("=" * 72)

    # ---------------- 1. 依赖检查 ----------------
    try:
        import langchain  # noqa: F401
        from langchain.agents import create_agent  # noqa: F401
    except ImportError as e:
        print(f"[跳过] 未安装 langchain（或版本过旧没有 create_agent）：{e}")
        print("       修复：pip install -U langchain langchain-openai")
        return

    from app.core.config import settings

    print(f"\n【0】模型配置：主模型={settings.model_line_name} "
          f"base_url={settings.openai_api_base}")
    if not settings.dashscope_api_key:
        print("       ⚠️ 未配置 DASHSCOPE_API_KEY，模型调用会失败（下面会走降级提示）")

    # ---------------- 2. 普通对话：观察流式效果 ----------------
    try:
        await run_agent("你好，请用一句话介绍你能帮独居老人做什么。")
    except Exception as e:
        print(f"\n[降级] 主模型调用失败：{type(e).__name__}: {e}")
        print("       下一步会尝试 get_text_by_fallback（主 → 备用 → 本地 → 静态文案）…")
        try:
            from app.ai.model.my_model import MyModel

            print("【降级回答】" + MyModel.get_text_by_fallback("你好，请用一句话介绍你自己。"))
        except Exception as e2:
            print(f"[跳过] 降级链也不可用：{e2}")
        return

    # ---------------- 3. 工具调用：观察 send_email_tool 被真实触发 ----------------
    print("\n【提示】下面这句会触发 send_email_tool（需要 .env 里的 EMAIL_* 配置才能真发出）")
    try:
        await run_agent("请给 767920412@qq.com 发一封测试邮件，主题是\"健康管家自测\"，"
                        "正文写：这是一封来自 create_agent 自测脚本的邮件。")
    except Exception as e:
        print(f"\n[降级] 工具调用失败：{type(e).__name__}: {e}")
        print("       常见原因：EMAIL_HOST / EMAIL_TO / EMAIL_PASSWORD 未配置，"
              "或网络无法访问 SMTP 服务器。")

    print("\n" + "=" * 72)
    print("create_agent 自测结束。要点回顾：")
    print("  · create_agent(model, tools, system_prompt) 三个入参与契约一致")
    print("  · astream(..., stream_mode='messages') 是 SSE 打字机的数据源")
    print("  · 工具调用会在流里以 tool_call_chunks / ToolMessage 形式出现")
    print("  · 主模型失败时可用 MyModel.get_text_by_fallback 做三级降级")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main_async())
