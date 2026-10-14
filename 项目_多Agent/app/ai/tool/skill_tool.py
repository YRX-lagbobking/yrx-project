"""
Skill 技能加载工具（契约第 9、10 节 / 需求 FR-12）

业务语义（渐进式加载）：
    技能以目录形式存放：`app/skills/registry/<skill_name>/SKILL.md`，
    含 YAML front-matter（name、description）+ Markdown 指令正文。
    1. Agent 启动时只把技能元数据（name/description）写进系统提示词 —— 轻量；
    2. LLM 判断用户问题命中某技能后，调用本工具 `load_skill_tool(skill_name, arguments)`
       拉取完整指令正文（`{{arguments}}` 会被替换成真实请求）；
    3. LLM 按指令依次调用 MCP 工具 / 本地工具完成业务闭环。

典型链路（需求 5.12 执行示例）：
    "帮我给张三发一封用药提醒邮件"
      → 匹配 medication_reminder → load_skill_tool
      → get_user_profile（MCP）→ get_medication_schedule（MCP）→ send_email（本地工具）→ 汇总回复

容错：
    - `app/skills/skill_loader.py` 由 T6 负责实现，可能尚未落地 → 延迟导入 + 友好提示；
    - 技能不存在 → 返回友好文本（列出可用技能），不抛异常。
"""
from langchain.tools import tool

from app.ai.tool.schema.skill_schema import SkillSchema
from app.core.logger import logger


def _list_available_skills() -> list[str]:
    """尽力列出已注册技能名（skill_loader 不可用时返回空列表）"""
    try:
        from app.skills.skill_loader import list_skills

        skills = list_skills() or []
        names: list[str] = []
        for item in skills:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
            elif isinstance(item, str):
                names.append(item)
        return names
    except Exception as e:
        logger.warning(f"读取技能列表失败：{e}")
        return []


def load_skill(skill_name: str, arguments: str = "") -> str:
    """
    加载技能指令正文（普通函数版，供节点/脚本复用；工具版见 load_skill_tool）

    返回：SKILL.md 指令正文；失败返回中文友好提示（永不抛异常）。
    """
    skill_name = (skill_name or "").strip()
    if not skill_name:
        return "未指定技能名称，请提供要加载的技能名。"

    try:
        from app.skills.skill_loader import load_skill as _load     # 延迟导入：T6 负责实现
    except Exception as e:
        logger.error(f"技能加载器不可用：{e}")
        return (
            "技能加载器暂不可用（app/skills/skill_loader.py 未就绪）。"
            "请直接用内置工具完成用户需求，并向用户说明技能功能暂不可用。"
        )

    try:
        content = _load(skill_name, arguments or "")
    except Exception as e:
        logger.error(f"加载技能 {skill_name} 异常：{e}")
        return f"加载技能「{skill_name}」时出错：{e}"

    if not content or not str(content).strip():
        available = _list_available_skills()
        hint = f"当前可用技能：{'、'.join(available)}" if available else "当前没有加载到任何技能"
        logger.warning(f"技能不存在或内容为空：{skill_name}")
        return f"没有找到名为「{skill_name}」的技能。{hint}。请换一个技能，或直接用内置工具回答用户。"

    return str(content)


@tool(args_schema=SkillSchema)
def load_skill_tool(skill_name: str, arguments: str = "") -> str:
    """
    加载指定技能的完整操作指令（渐进式加载 Skill）

    什么时候用：
        当用户请求属于"用药提醒邮件、日报、周报、情感安慰"等固定套路任务时，
        先调用本工具拿到该技能的步骤说明，再严格按说明依次调用其它工具。

    参数：
        skill_name 技能名称，例如 medication_reminder（用药提醒邮件）、
                   daily_report（日报）、weekly_report（周报）、emotion_comfort（情感安慰）
        arguments  把用户的原话或关键参数带进来，会替换指令里的 {{arguments}} 占位符
    """
    return load_skill(skill_name, arguments)


if __name__ == "__main__":
    # 自测：skill_loader 尚未落地时应返回友好提示，不抛异常
    print("已知技能列表：", _list_available_skills())
    print("加载不存在的技能：", load_skill("not_exist_skill"))
    print("空技能名：", load_skill(""))
    print("工具出口：", load_skill_tool.invoke({"skill_name": "weekly_report", "arguments": "上周总结"}))
