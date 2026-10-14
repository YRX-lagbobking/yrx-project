"""
Skill 技能包（渐进式加载）

设计说明（FR-12）：
    1. 每个技能是一个目录：app/skills/registry/<skill_name>/SKILL.md；
    2. SKILL.md = YAML front-matter（name/description）+ Markdown 指令正文；
    3. Agent 启动时只加载元数据（list_skills 很轻），LLM 判断命中后才 load_skill
       拉取完整指令正文——这就是"渐进式披露"，避免一次性把全部技能塞进提示词；
    4. 正文里的 {{arguments}} 是用户原始请求的占位符，由 load_skill 负责替换。

对外只暴露三个函数（契约第 10 节），实现见 skill_loader.py。
"""
from app.skills.skill_loader import get_skill_meta, list_skills, load_skill

__all__ = ["list_skills", "get_skill_meta", "load_skill"]
