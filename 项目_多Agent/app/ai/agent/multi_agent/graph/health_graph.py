"""
健康管家多智能体图（无并发版，用于本地调试与画图）

图结构（Supervisor-Worker）：
                        START
                          │
                       planner（LLM 任务拆解）
                          │
                       manager（主管调度，Command 动态路由）
        ┌────────┬────────┼────────┬────────┬────────┬────────┐
        ▼        ▼        ▼        ▼        ▼        ▼        ▼
     intent  data_check   rag     risk    report   remind   notify   chat
        └────────┴────────┴────────┴────────┴────────┴────────┘
                          │（每个 Worker 干完活都回到 manager）
                       manager
                          │ 队列空
                     summarize → END

说明：
    1. 没有并发场景时用本类；生产（多用户并发）请用 health_graph_agent.HealthGraphAgent，
       它在图之外多了"会话归属校验 + Redis 分布式锁 + 四层记忆更新"。
    2. 节点全部为 async，只有 manager 是同步函数（纯代码决策，返回 Command）。
"""
import asyncio

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.ai.agent.multi_agent.node.chat_node import chat_node
from app.ai.agent.multi_agent.node.data_check_node import data_check_node
from app.ai.agent.multi_agent.node.manager_node import manager_node
from app.ai.agent.multi_agent.node.notify_node import notify_node
from app.ai.agent.multi_agent.node.planner_node import planner_node
from app.ai.agent.multi_agent.node.rag_node import rag_node
from app.ai.agent.multi_agent.node.remind_node import remind_node
from app.ai.agent.multi_agent.node.report_node import report_node
from app.ai.agent.multi_agent.node.risk_node import risk_node
from app.ai.agent.multi_agent.node.summarize_node import summarize_node
from app.ai.agent.multi_agent.state.health_state import HealthState


class HealthGraph:
    """健康管家多智能体（无并发版）"""

    def __init__(self, checkpoint):
        self.memory = checkpoint
        self.agent = self.get_agent()

    # 构建图
    def get_agent(self):
        graph = StateGraph(HealthState)

        # 添加节点
        graph.add_node("planner", planner_node)
        graph.add_node("manager", manager_node)
        graph.add_node("data_check", data_check_node)
        graph.add_node("rag", rag_node)
        graph.add_node("risk", risk_node)
        graph.add_node("report", report_node)
        graph.add_node("remind", remind_node)
        graph.add_node("notify", notify_node)
        graph.add_node("summarize", summarize_node)
        graph.add_node("chat", chat_node)

        # 画边：入口 → planner → manager
        graph.add_edge(START, "planner")
        graph.add_edge("planner", "manager")

        # 所有 Worker 干完活都回到主管，形成"调度循环"
        graph.add_edge("data_check", "manager")
        graph.add_edge("rag", "manager")
        graph.add_edge("risk", "manager")
        graph.add_edge("report", "manager")
        graph.add_edge("remind", "manager")
        graph.add_edge("notify", "manager")
        graph.add_edge("chat", "manager")

        # 汇总结束
        graph.add_edge("summarize", END)

        # 编译
        self.agent = graph.compile(checkpointer=self.memory)
        return self.agent

    # 对话
    async def chat(self, question, user_id, session_id):
        print(f"进入聊天:用户问题：{question},用户ID:{user_id},会话ID:{session_id}")
        # 构建用户问题 + 初始状态
        user_msg = {
            "messages": [HumanMessage(content=question)],
            "user_id": user_id,
            "session_id": session_id,
            "role": "elder",
            "task_queue": [],
            # task_results 带合并 reducer：清空必须用 __reset__ 标记（合并语义下给 {} 不清空）；
            # planner 每轮也会再发一次同样的标记，双保险。
            "task_results": {"__reset__": True},
            "retry_count": 0,
            "health_step": "start",
        }
        # 配置检测点（thread_id 即会话隔离键）
        config = {"configurable": {"thread_id": session_id}}
        # 只开 custom 通道（原因见 health_graph_agent.chat 的注释）：
        # messages 通道会推"节点 return 的 AIMessage（内部状态）"与"节点内 LLM token"，
        # 与各节点 writer() 推的内容重复，导致前端重复输出并混入内部状态。
        async for chunk in self.agent.astream(user_msg, config, stream_mode="custom"):
            yield chunk if isinstance(chunk, str) else str(chunk)

    # 画图
    def draw(self, file_name: str = "健康管家图.png"):
        data = self.agent.get_graph().draw_mermaid_png()
        with open(file_name, "wb") as f:
            f.write(data)


if __name__ == "__main__":
    from langgraph.checkpoint.memory import InMemorySaver

    # async def test():
    #     agent = HealthGraph(InMemorySaver())
    #     async for c in agent.chat("我最近血压怎么样？顺便提醒我吃药", "u001", "s001"):
    #         print(c, end="")
    #
    # asyncio.run(test())
    # 创建图实例
    graph = HealthGraph(InMemorySaver())

    # 调用 draw 方法生成 PNG
    graph.draw("健康管家图.png")
    print("图已生成：健康管家图.png")