"""
Skill 技能机制自测（FR-12 / AC-22）

推荐运行方式（在项目根目录下执行）：
    python -m app.test.test_skill
    python app/test/test_skill.py

演示内容：
    1. list_skills()            —— 只加载元数据（渐进式披露的第一层）
    2. get_skill_meta(...)      —— 取单个技能元数据（不存在返回 None）
    3. load_skill(name, args)   —— 取完整指令正文，{{arguments}} 被替换为用户请求
    4. 错误分支：加载不存在的技能抛 FileNotFoundError（上层会捕获降级）

依赖：PyYAML（requirements.txt 已声明）。缺失时打印提示并退出。
"""
import os
import sys

# ---------- 项目根目录加入 sys.path，保证直接 python app/test/xxx.py 也能跑 ----------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> None:
    print("=" * 72)
    print("Skill 技能机制自测（FR-12 / AC-22）")
    print("=" * 72)

    try:
        from app.skills.skill_loader import get_skill_meta, list_skills, load_skill
    except ImportError as e:
        print(f"[跳过] 技能加载器导入失败（通常是缺少 PyYAML）：{e}")
        print("       修复：pip install pyyaml")
        return

    # ---------------- 1. 列出全部技能（只读元数据，轻量） ----------------
    print("\n【1】list_skills()：只加载 YAML 元数据，不读正文（渐进式披露第一层）")
    skills = list_skills()
    if not skills:
        print("     没有发现任何技能，请检查 app/skills/registry/<skill_name>/SKILL.md 是否存在")
        return
    for item in skills:
        print(f"     - {item['name']:<20} {item['description']}")
        print(f"       path = {item['path']}")

    # ---------------- 2. 取单个技能的元数据 ----------------
    demo_skill = "medication_reminder"
    print(f"\n【2】get_skill_meta('{demo_skill}')：命中示例")
    meta = get_skill_meta(demo_skill)
    if meta:
        print(f"     name        = {meta['name']}")
        print(f"     description = {meta['description']}")
        print(f"     正文长度    = {len(meta['body'])} 字符")
    else:
        print("     未取到元数据")

    print("\n     反例：get_skill_meta('不存在的技能') →", get_skill_meta("not_exist_skill"))

    # ---------------- 3. 加载完整指令正文（替换 {{arguments}}） ----------------
    arguments = "帮我给张三发一封用药提醒邮件"
    print(f"\n【3】load_skill('{demo_skill}', '{arguments}')：取完整指令正文")
    try:
        body = load_skill(demo_skill, arguments)
    except FileNotFoundError as e:
        print(f"     [跳过] {e}")
        return

    print(f"     正文长度        = {len(body)} 字符")
    print(f"     占位符是否残留  = {'{{arguments}}' in body}（应为 False）")
    print(f"     是否含用户请求  = {arguments in body}（应为 True）")
    print("     ---------- 正文前 18 行 ----------")
    for line in body.splitlines()[:18]:
        print("     | " + line)

    # 不传参数时应替换为空串（避免提示词里残留占位符）
    no_arg = load_skill(demo_skill)
    print(f"\n     load_skill('{demo_skill}') 无参数 → 占位符残留 = "
          f"{'{{arguments}}' in no_arg}（应为 False）")

    # ---------------- 4. 错误分支：技能不存在 ----------------
    print("\n【4】错误分支：load_skill('not_exist_skill') 应抛 FileNotFoundError")
    try:
        load_skill("not_exist_skill")
        print("     [异常] 竟然没有抛异常，请检查实现")
    except FileNotFoundError as e:
        print(f"     按预期捕获 FileNotFoundError：{e}")

    # ---------------- 5. AC-22 说明：新增技能无需改代码 ----------------
    print("\n【5】AC-22 说明：在 app/skills/registry/ 下新增一个目录 + SKILL.md，"
          "重新执行本脚本即可在列表中出现，无需修改任何 Python 代码。")

    print("\n" + "=" * 72)
    print("Skill 技能机制自测结束")
    print("=" * 72)


if __name__ == "__main__":
    main()
