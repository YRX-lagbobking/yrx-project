"""
路由分类器（同步函数，供 chat_router / voice_router 等需要"快速选路"的场景使用）

与 intent_node 的区别：
    - intent_node 是图内节点（async），输出"意图 + 情感"，服务于编排与情感陪伴；
    - router_agent 是图外工具函数（sync），只输出一个粗粒度路由，
      用于"要不要进多智能体图 / 走哪条轻量链路"的前置判断（也方便单测与对比实验）。

输出路由（RouterSchema.route）只能是：health / medication / emergency / report / chat

降级设计（照 exam_0914 的 router_agent 结构）：
    第一层 try：正常调用模型 → 解析结构化输出；
    第二层 try：重试一次（换用备用模型，应对主模型偶发超时/限流）；
    最终兜底：返回 "chat"（永不抛异常给上层）。
"""
from langchain.agents import create_agent

from app.ai.agent.multi_agent.schema.intent_schema import RouterSchema
from app.ai.model.my_model import MyModel
from app.ai.prompt.bulider_prompt import BuilderPromptYaml
from app.core.logger import logger

# 读取外部提示词（模块顶层加载）
prompt = BuilderPromptYaml.get_prompt("router_agent.yaml")

# 路由白名单（与 RouterSchema 描述一致）
ROUTES = ("health", "medication", "emergency", "report", "chat")


def _parse_route(result) -> str:
    """把 create_agent 的返回解析成路由字符串；非法一律回退 chat"""
    schema = None
    if isinstance(result, dict):
        schema = result.get("structured_response")
    data = schema.model_dump() if schema is not None else {}
    route = str(data.get("route") or "").strip()
    if route not in ROUTES:
        logger.warning(f"路由分类输出非法 route={route}，回退 chat")
        return "chat"
    return route


def router_agent(question: str) -> str:
    """同步路由分类：返回 health / medication / emergency / report / chat"""
    # ---------------- 第一层：主模型 ----------------
    try:
        agent = create_agent(
            model=MyModel.get_model(),
            system_prompt=prompt,
            response_format=RouterSchema,
        )
        rs = agent.invoke({"messages": [{"role": "user", "content": question}]})
        route = _parse_route(rs)
        logger.info(f"[Router] 问题「{question}」→ 路由 {route}")
        return route
    except Exception as e:
        logger.error(f"[Router] 主模型路由失败，尝试备用模型：{e}")

    # ---------------- 第二层：备用模型 ----------------
    try:
        agent = create_agent(
            model=MyModel.get_backup_model(),
            system_prompt=prompt,
            response_format=RouterSchema,
        )
        rs = agent.invoke({"messages": [{"role": "user", "content": question}]})
        route = _parse_route(rs)
        logger.warning(f"[Router] 备用模型路由成功 → {route}")
        return route
    except Exception as e:
        logger.error(f"[Router] 备用模型路由也失败，兜底 chat：{e}")

    # ---------------- 最终兜底 ----------------
    return "chat"


if __name__ == "__main__":
    # 自测说明：不连外网时 router_agent 会走两层异常并返回 chat，这正是降级预期。
    print("提示词前 100 字：", prompt[:100].replace("\n", " "))
    for q in ["我今天血压怎么样", "提醒我吃药", "我喘不上气", "帮我出周报", "陪我聊聊天"]:
        try:
            print(f"{q}  ->  {router_agent(q)}")
        except Exception as e:  # 理论上不会走到这里（函数内部已兜底）
            print(f"{q}  ->  异常（异常兜底失效，需检查）：{e}")
