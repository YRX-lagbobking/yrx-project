"""
项目自检脚本（交付前/提交前必跑）

三项检查：
    1. 结构检查：契约第 1 节列出的关键文件/目录是否存在；
    2. 语法检查：对项目内所有 .py 执行 py_compile（等价于 python -m compileall）；
    3. 导入检查：用 AST 解析每个模块的 `from app.x.y import a, b` / `import app.x.y`，
       校验"目标模块文件存在"且"被导入的名字在目标模块顶层有定义"。
       只校验本项目自有包（app.* 与 mcp_server.*），第三方依赖不做校验。

为什么需要它：
    本项目由多人（或多个 AI 协作单元）并行开发，跨包接口靠文档约定，
    仅凭 py_compile 无法发现"导入了不存在的符号"这类问题，本脚本把这类问题提前暴露。

用法（在项目根目录执行）：
    python scripts/check_project.py
退出码：0 全部通过；1 存在错误（错误明细会打印）。
"""
import ast
import os
import sys
from typing import Dict, List, Optional, Set, Tuple

# 项目根目录 = 本文件所在目录的上一级
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Windows 控制台默认可能是 GBK/cp1252，强制用 UTF-8 输出，避免中文报告乱码或抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# 自有包前缀（只校验这些包的导入）
OWN_PACKAGES = ("app", "mcp_server")

# 契约第 1 节要求存在的关键文件
EXPECTED_FILES = [
    "需求文档.md",
    "README.md",
    "requirements.txt",
    ".env.example",
    "Dockerfile",
    "docker-compose.yml",
    "docs/开发规范与接口契约.md",
    "docs/系统架构图.md",
    "docs/接口文档.md",
    "docs/数据库设计.md",
    "sql/init.sql",
    "scripts/check_project.py",
    "app/main.py",
    "app/core/config.py",
    "app/core/logger.py",
    "app/core/security.py",
    "app/core/celery_app.py",
    "app/core/fallback.py",
    "app/core/health_check.py",
    "app/core/detect/anomaly_detector.py",
    "app/core/detect/rule_detector.py",
    "app/core/detect/trend_detector.py",
    "app/core/detect/outlier_detector.py",
    "app/core/task/notify_task.py",
    "app/core/task/detect_task.py",
    "app/dao/base_dao.py",
    "app/dao/user_dao.py",
    "app/dao/health_dao.py",
    "app/dao/medication_dao.py",
    "app/dao/alert_dao.py",
    "app/ai/model/my_model.py",
    "app/ai/prompt/bulider_prompt.py",
    "app/ai/tool/mysql_tool.py",
    "app/ai/tool/send_email_tool.py",
    "app/ai/tool/influx_tool.py",
    "app/ai/tool/rag_tool.py",
    "app/ai/tool/mcp_tool.py",
    "app/ai/tool/skill_tool.py",
    "app/ai/agent/vosk_agent.py",
    "app/ai/agent/tts_agent.py",
    "app/ai/agent/memory/manager/memory_manager.py",
    "app/ai/agent/memory/manager/prompt_builder.py",
    "app/ai/agent/memory/manager/session_mananger.py",
    "app/ai/agent/memory/retrieval/SummaryAgent.py",
    "app/ai/agent/memory/retrieval/long_agent.py",
    "app/ai/agent/memory/retrieval/profile_agent.py",
    "app/ai/agent/memory/save/window_memory.py",
    "app/ai/agent/memory/save/summary_memory.py",
    "app/ai/agent/memory/save/long_memory.py",
    "app/ai/agent/memory/save/profile_memory.py",
    "app/ai/agent/multi_agent/graph/health_graph.py",
    "app/ai/agent/multi_agent/graph/health_graph_agent.py",
    "app/ai/agent/multi_agent/state/health_state.py",
    "app/ai/agent/multi_agent/schema/intent_schema.py",
    "app/ai/agent/multi_agent/schema/plan_schema.py",
    "app/ai/agent/multi_agent/schema/risk_schema.py",
    "app/ai/agent/multi_agent/schema/report_schema.py",
    "app/ai/agent/multi_agent/node/manager_node.py",
    "app/ai/agent/multi_agent/node/planner_node.py",
    "app/ai/agent/multi_agent/node/intent_node.py",
    "app/ai/agent/multi_agent/node/router_agent.py",
    "app/ai/agent/multi_agent/node/data_check_node.py",
    "app/ai/agent/multi_agent/node/rag_node.py",
    "app/ai/agent/multi_agent/node/risk_node.py",
    "app/ai/agent/multi_agent/node/report_node.py",
    "app/ai/agent/multi_agent/node/remind_node.py",
    "app/ai/agent/multi_agent/node/notify_node.py",
    "app/ai/agent/multi_agent/node/summarize_node.py",
    "app/ai/agent/multi_agent/node/chat_node.py",
    "app/skills/skill_loader.py",
    "app/skills/registry/medication_reminder/SKILL.md",
    "app/skills/registry/weekly_report/SKILL.md",
    "app/skills/registry/daily_report/SKILL.md",
    "app/skills/registry/emotion_comfort/SKILL.md",
    "app/web/chat_router/chat_router.py",
    "app/web/voice_router/voice_router.py",
    "app/web/websocker_router/websocket_notify.py",
    "app/web/report_router/report_router.py",
    "app/web/alert_router/alert_router.py",
    "app/web/user_router/user_router.py",
    "app/web/system_router/system_router.py",
    "app/web/default_page_router/default_page_router.py",
    "app/html/login.html",
    "app/html/chat.html",
    "app/html/report.html",
    "app/html/family.html",
    "mcp_server/mcp_health_server.py",
    "mcp_server/tools/profile_tool.py",
    "mcp_server/tools/medication_tool.py",
    "mcp_server/tools/vitals_tool.py",
    "mcp_server/tools/knowledge_tool.py",
    "mcp_server/tools/report_tool.py",
    "mcp_server/tools/notify_tool.py",
]

# 契约要求存在的 12 个提示词 YAML
EXPECTED_PROMPTS = [
    "planner_node.yaml", "manager_node.yaml", "intent_node.yaml", "router_agent.yaml",
    "data_check_node.yaml", "rag_node.yaml", "risk_node.yaml", "report_node.yaml",
    "remind_node.yaml", "notify_node.yaml", "summarize_node.yaml", "chat_node.yaml",
]

# 需要存在的包目录（每个包目录必须有 __init__.py）
EXPECTED_PACKAGES = [
    "app", "app/core", "app/core/detect", "app/core/task", "app/dao", "app/skills",
    "app/ai", "app/ai/model", "app/ai/prompt", "app/ai/tool", "app/ai/tool/schema",
    "app/ai/agent", "app/ai/agent/memory", "app/ai/agent/memory/manager",
    "app/ai/agent/memory/retrieval", "app/ai/agent/memory/save",
    "app/ai/agent/multi_agent", "app/ai/agent/multi_agent/graph",
    "app/ai/agent/multi_agent/node", "app/ai/agent/multi_agent/schema",
    "app/ai/agent/multi_agent/state", "app/web", "app/web/chat_router",
    "app/web/voice_router", "app/web/websocker_router", "app/web/report_router",
    "app/web/alert_router", "app/web/user_router", "app/web/system_router",
    "app/web/default_page_router", "app/test", "mcp_server", "mcp_server/tools",
]


def iter_py_files() -> List[str]:
    """遍历项目内所有 .py（跳过缓存与虚拟环境）"""
    result = []
    skip_dirs = {"__pycache__", ".git", ".idea", "node_modules", ".venv", "venv", "chroma_db"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for name in filenames:
            if name.endswith(".py"):
                result.append(os.path.join(dirpath, name))
    return sorted(result)


def module_to_path(module: str, root: str = ROOT) -> Optional[str]:
    """把点分模块名解析成文件路径（包则找 __init__.py）"""
    relative = module.replace(".", os.sep)
    as_file = os.path.join(root, relative + ".py")
    if os.path.isfile(as_file):
        return as_file
    as_package = os.path.join(root, relative, "__init__.py")
    if os.path.isfile(as_package):
        return as_package
    return None


def collect_top_level_names(tree: ast.Module) -> Set[str]:
    """
    收集模块顶层定义的符号名。

    会递归进入 try / if / with 等语句块（因为"可选依赖延迟导入"常写成
    try: from x import y  except ImportError: y = None），但不会进入函数与类内部。
    """
    names: Set[str] = set()

    def visit(nodes) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    names.update(_target_names(target))
            elif isinstance(node, ast.AnnAssign):
                names.update(_target_names(node.target))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, (ast.Try, ast.If, ast.With, ast.AsyncWith)):
                visit(node.body)
                for handler in getattr(node, "handlers", []):
                    visit(handler.body)
                visit(getattr(node, "orelse", []))
                visit(getattr(node, "finalbody", []))

    visit(tree.body)
    return names


def _target_names(target: ast.expr) -> Set[str]:
    """取赋值目标名（支持元组解包）"""
    names: Set[str] = set()
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            names.update(_target_names(element))
    return names


def main() -> int:
    errors: List[str] = []
    warnings: List[str] = []

    # ---------- 1. 结构检查 ----------
    for relative in EXPECTED_FILES:
        if not os.path.isfile(os.path.join(ROOT, relative)):
            errors.append(f"[结构] 缺少文件：{relative}")
    for relative in EXPECTED_PROMPTS:
        if not os.path.isfile(os.path.join(ROOT, "app", "ai", "prompt", relative)):
            errors.append(f"[结构] 缺少提示词：app/ai/prompt/{relative}")
    for relative in EXPECTED_PACKAGES:
        if not os.path.isfile(os.path.join(ROOT, relative, "__init__.py")):
            errors.append(f"[结构] 缺少包初始化文件：{relative}/__init__.py")

    # ---------- 2. 语法检查 ----------
    # 用内建 compile() 做纯语法检查（与 python -m py_compile 等价，但不产生 __pycache__ 垃圾文件）
    py_files = iter_py_files()
    compiled = 0
    for path in py_files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            compile(source, path, "exec")
            compiled += 1
        except SyntaxError as e:
            errors.append(
                f"[语法] 第 {e.lineno} 行语法错误：{os.path.relpath(path, ROOT)} -> {e.msg}"
            )
        except Exception as e:  # noqa: BLE001
            errors.append(f"[语法] 编译异常：{os.path.relpath(path, ROOT)} -> {e}")

    # ---------- 3. 导入检查 ----------
    tree_cache: Dict[str, Optional[ast.Module]] = {}
    names_cache: Dict[str, Set[str]] = {}

    def get_tree(module: str) -> Optional[ast.Module]:
        if module in tree_cache:
            return tree_cache[module]
        path = module_to_path(module)
        tree = None
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=path)
            except SyntaxError as e:
                errors.append(f"[导入] 目标模块语法错误：{module} -> {e}")
        tree_cache[module] = tree
        return tree

    def get_names(module: str) -> Set[str]:
        if module in names_cache:
            return names_cache[module]
        tree = get_tree(module)
        names = collect_top_level_names(tree) if tree else set()
        names_cache[module] = names
        return names

    # 检查本项目内部导入的总数（用于报告）
    checked_imports = 0
    for path in py_files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
        except SyntaxError:
            continue  # 语法错误已在第 2 步报告
        relative = os.path.relpath(path, ROOT).replace(os.sep, "/")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not alias.name.startswith(OWN_PACKAGES):
                        continue
                    checked_imports += 1
                    if module_to_path(alias.name) is None:
                        errors.append(f"[导入] {relative}:{node.lineno} 模块不存在：{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if not module.startswith(OWN_PACKAGES):
                    continue
                checked_imports += 1
                if module_to_path(module) is None:
                    errors.append(f"[导入] {relative}:{node.lineno} 模块不存在：{module}")
                    continue
                available = get_names(module)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    if alias.name in available:
                        continue
                    # `from package import submodule` 也是合法的：包内的子模块文件存在即可
                    if module_to_path(f"{module}.{alias.name}") is not None:
                        continue
                    errors.append(
                        f"[导入] {relative}:{node.lineno} {module} 中不存在符号：{alias.name}"
                    )

    # ---------- 4. 汇总 ----------
    print("=" * 74)
    print("智能健康管家多智能体系统 —— 项目自检报告")
    print("=" * 74)
    print(f"项目根目录      : {ROOT}")
    print(f"Python 文件数   : {len(py_files)}")
    print(f"语法检查通过    : {compiled}/{len(py_files)}")
    print(f"内部导入检查    : {checked_imports} 条（app.* / mcp_server.*）")
    print(f"错误            : {len(errors)}")
    print(f"警告            : {len(warnings)}")
    if warnings:
        print("-" * 74)
        for warning in warnings:
            print(warning)
    if errors:
        print("-" * 74)
        for error in errors:
            print(error)
        print("-" * 74)
        print("自检失败：请修复以上问题")
        return 1
    print("-" * 74)
    print("自检通过 ✅  结构 / 语法 / 内部导入 三项全部正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
