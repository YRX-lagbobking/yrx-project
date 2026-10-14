"""
路由与白名单一致性自测（单一数据源守卫）

背景：
    历史上"任务名 → Worker 节点"这份映射被写在 4 个地方
    （schema 描述 / planner 提示词 / planner.TASK_WHITELIST / manager.ROUTE_MAP），
    改漏一处就出现"名单不同步"——intent 节点被合并进 planner 时就是这样：
    白名单/提示词里还有 intent，而图里已经没有这个节点了。

本测试断言：
    1. planner.TASK_WHITELIST 与 schema.TASK_ROUTES 的 key 完全一致；
    2. manager.ROUTE_MAP 与 schema.TASK_ROUTES 完全一致；
    3. 每个路由目标（Worker 节点名）都真的注册在图中（防"路由到不存在的节点"）；
    4. 提示词 planner_node.yaml 里出现的 {"task": "xxx"} 都在 TASK_ROUTES 内；
    5. schema 的 TaskItem 描述里列出的任务名与 TASK_ROUTES 一致。

运行：python -m app.test.test_routes
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _yaml_task_names(path: str) -> set:
    """从 planner 提示词里抠出所有 {"task": "xxx"} 用到的任务名"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return set(re.findall(r'"task"\s*:\s*"([A-Za-z_]+)"', text))


def main() -> int:
    print("=" * 72)
    print("路由/白名单一致性自测（单一数据源：schema/plan_schema.TASK_ROUTES）")
    print("=" * 72)

    from langgraph.checkpoint.memory import InMemorySaver

    from app.ai.agent.multi_agent.graph.health_graph_agent import HealthGraphAgent
    from app.ai.agent.multi_agent.node.manager_node import ROUTE_MAP
    from app.ai.agent.multi_agent.node.planner_node import TASK_WHITELIST
    from app.ai.agent.multi_agent.schema.plan_schema import TASK_ROUTES, TaskItem

    ok = True
    print("\n【1】TASK_ROUTES（唯一数据源）")
    for task, node in TASK_ROUTES.items():
        print(f"    {task:<12} → {node}")

    print("\n【2】planner.TASK_WHITELIST 是否由 TASK_ROUTES 派生")
    same = tuple(TASK_ROUTES.keys()) == tuple(TASK_WHITELIST)
    print(f"    {'✅' if same else '❌'} 白名单 = {TASK_WHITELIST}")
    ok &= same

    print("\n【3】manager.ROUTE_MAP 是否与 TASK_ROUTES 一致")
    same = dict(TASK_ROUTES) == dict(ROUTE_MAP)
    print(f"    {'✅' if same else '❌'} ROUTE_MAP = {ROUTE_MAP}")
    if not same:
        print(f"        差异：TASK_ROUTES={dict(TASK_ROUTES)}\n              ROUTE_MAP={dict(ROUTE_MAP)}")
    ok &= same

    print("\n【4】每个路由目标节点是否都注册在图里")
    graph = HealthGraphAgent(InMemorySaver(), None).agent.get_graph()
    nodes = set(graph.nodes.keys())
    print(f"    图节点：{sorted(nodes)}")
    for task, node in TASK_ROUTES.items():
        exists = node in nodes
        print(f"    {'✅' if exists else '❌'} {task:<12} → {node}")
        ok &= exists

    print("\n【5】提示词 planner_node.yaml 里的任务名是否都在 TASK_ROUTES 内")
    yaml_path = os.path.join(_ROOT, "app", "ai", "prompt", "planner_node.yaml")
    used = _yaml_task_names(yaml_path)
    unknown = sorted(used - set(TASK_ROUTES.keys()))
    print(f"    yaml 用到的任务名：{sorted(used)}")
    print(f"    {'✅ 无未知任务名' if not unknown else '❌ 出现未登记的任务名：' + str(unknown)}")
    ok &= not unknown

    print("\n【6】schema 描述里的任务名是否与 TASK_ROUTES 一致")
    desc = TaskItem.model_fields["task"].description or ""
    missing = [t for t in TASK_ROUTES if t not in desc]
    print(f"    描述：{desc}")
    print(f"    {'✅ 覆盖全部任务' if not missing else '❌ 缺：' + str(missing)}")
    ok &= not missing

    print("\n" + "=" * 72)
    print("自测" + ("通过 ✅（名单已单一数据源化）" if ok else "未通过 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
