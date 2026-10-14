"""
Skill 加载工具入参模型（Pydantic）

对应契约第 9、10 节：
    `load_skill_tool(skill_name, arguments="")`
    → `app/skills/skill_loader.py::load_skill(skill_name, arguments)`

业务语义（FR-12 渐进式加载）：
    Agent 启动时只加载技能的元数据（name/description）；
    当 LLM 判断用户问题命中某技能后，再调用本工具拉取 SKILL.md 完整指令正文，
    arguments 会被替换进正文中的 `{{arguments}}` 占位符。
"""
from pydantic import BaseModel, Field


class SkillSchema(BaseModel):
    """load_skill_tool 的入参"""

    skill_name: str = Field(
        ...,
        description=(
            "技能名称，必须是 app/skills/registry 下已注册的技能目录名，"
            "例如 medication_reminder / weekly_report / daily_report / emotion_comfort"
        ),
        min_length=1,
    )
    arguments: str = Field(
        "",
        description="技能执行所需的补充参数（自然语言即可），会替换 SKILL.md 正文里的 {{arguments}} 占位符",
    )


if __name__ == "__main__":
    # 自测：默认 arguments 为空串
    print(SkillSchema(skill_name="weekly_report").model_dump())
    print(
        SkillSchema(
            skill_name="medication_reminder",
            arguments="给张三发一封用药提醒邮件",
        ).model_dump()
    )
