"""
Skill 技能加载器（契约第 10 节，签名逐字一致）

职责：
    1. list_skills()                —— 扫描技能目录，只读 YAML front-matter（轻量）；
    2. get_skill_meta(skill_name)   —— 取单个技能的元数据，不存在返回 None；
    3. load_skill(skill_name, args) —— 取完整指令正文，并把 {{arguments}} 替换为
                                       本次用户的原始请求。

SKILL.md 格式：

    ---
    name: medication_reminder
    description: 给指定老人发送用药提醒邮件
    ---
    # 正文（Markdown 中文指令）
    ... 用户原始请求：{{arguments}} ...

设计要点：
    - 路径用 os.path.dirname(os.path.abspath(__file__)) 推导，禁止硬编码绝对路径，
      整个项目目录拷贝到任何盘符都能跑；
    - 技能"新增即可用"（AC-22）：registry 下增加一个目录 + SKILL.md，无需改代码；
    - 找不到技能抛 FileNotFoundError（上层 fallback / tool 会捕获并降级），
      get_skill_meta 找不到返回 None（调用方可以安全地做 if 判断）。
"""
import os

import yaml

# 技能注册目录：app/skills/registry
SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry")

# 技能正文里的参数占位符
ARG_PLACEHOLDER = "{{arguments}}"


def _skill_path(skill_name: str) -> str:
    """拼出某个技能的 SKILL.md 绝对路径（不校验存在性）"""
    return os.path.join(SKILLS_DIR, skill_name, "SKILL.md")


def _parse_skill_file(file_path: str) -> tuple[dict, str]:
    """
    解析 SKILL.md，返回 (元数据 dict, 指令正文 str)。

    容错策略：
        - 没有 front-matter 时元数据为空 dict，正文取全文（不抛异常）；
        - front-matter 不是合法 YAML 时同样退化为"正文=全文"，
          保证一个格式写错的技能不会让整个技能列表崩掉。
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 统一换行符，避免 Windows CRLF 影响 front-matter 切分
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    meta: dict = {}
    body = content

    if content.startswith("---"):
        # 只切分前两个 "---"，正文里出现的水平分割线不受影响
        parts = content.split("---", 2)
        if len(parts) == 3:
            raw_meta, body = parts[1], parts[2]
            try:
                loaded = yaml.safe_load(raw_meta)
                if isinstance(loaded, dict):
                    meta = loaded
            except yaml.YAMLError:
                # YAML 写错时不阻断，正文照常可用
                meta = {}

    return meta, body.lstrip("\n")


def list_skills() -> list[dict]:
    """
    列出全部技能（只读元数据，轻量，供 LLM 判断用哪个技能）。

    返回：
        [{"name": "medication_reminder", "description": "...", "path": "<SKILL.md 绝对路径>"}, ...]
        目录不存在或无技能时返回 []。
    """
    skills: list[dict] = []
    if not os.path.isdir(SKILLS_DIR):
        return skills

    for entry in sorted(os.listdir(SKILLS_DIR)):
        skill_dir = os.path.join(SKILLS_DIR, entry)
        skill_file = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isdir(skill_dir) or not os.path.isfile(skill_file):
            continue
        try:
            meta, _ = _parse_skill_file(skill_file)
        except OSError:
            # 单个技能读失败不影响其它技能
            continue
        skills.append(
            {
                # 元数据里没写 name 时用目录名兜底（目录名就是技能标识）
                "name": str(meta.get("name") or entry),
                "description": str(meta.get("description") or ""),
                "path": skill_file,
            }
        )
    return skills


def get_skill_meta(skill_name: str) -> dict | None:
    """
    获取单个技能的元数据（含 name/description/body/path）。

    返回 None 表示技能不存在；元数据缺失的键用目录名与空串兜底。
    """
    skill_file = _skill_path(skill_name)
    if not os.path.isfile(skill_file):
        return None
    try:
        meta, body = _parse_skill_file(skill_file)
    except OSError:
        return None
    return {
        "name": str(meta.get("name") or skill_name),
        "description": str(meta.get("description") or ""),
        "body": body,
        "path": skill_file,
    }


def load_skill(skill_name: str, arguments: str = "") -> str:
    """
    加载技能完整指令正文，并把 {{arguments}} 替换为用户原始请求。

    参数：
        skill_name —— 技能目录名（app/skills/registry/<skill_name>/SKILL.md）
        arguments  —— 用户原始请求；为空时占位符替换成空串

    返回：
        替换后的 Markdown 指令正文

    异常：
        FileNotFoundError —— 技能不存在（上层 skill_tool / fallback 会捕获并降级）
    """
    skill_file = _skill_path(skill_name)
    if not os.path.isfile(skill_file):
        raise FileNotFoundError(f"技能不存在：{skill_name}（期望文件 {skill_file}）")

    _, body = _parse_skill_file(skill_file)
    # 没传参数时替换为空串，避免提示词里残留 {{arguments}} 影响模型理解
    return body.replace(ARG_PLACEHOLDER, arguments or "")


if __name__ == "__main__":
    # 自测：列出技能 → 取元数据 → 加载正文（对应 AC-22 的"新增即可用"）
    print("=" * 60)
    print("技能列表：")
    for item in list_skills():
        print(f"  - {item['name']}: {item['description']}")
        print(f"    路径：{item['path']}")

    print("=" * 60)
    demo = "medication_reminder"
    print(f"技能元数据（{demo}）：")
    print(get_skill_meta(demo))

    print("=" * 60)
    print(f"加载技能正文（{demo}，带参数）：")
    print(load_skill(demo, "帮我给张三发一封用药提醒邮件"))

    print("=" * 60)
    print("不存在的技能：", get_skill_meta("not_exist_skill"))
    try:
        load_skill("not_exist_skill")
    except FileNotFoundError as e:
        print("按预期抛出 FileNotFoundError：", e)
