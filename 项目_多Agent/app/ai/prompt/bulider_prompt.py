"""
提示词构建器：把外置 YAML 提示词装配成 system prompt

YAML 固定 5 个键：
    role     角色定义（字符串）
    task     任务列表
    rule     规则列表
    output   输出要求列表
    example  示例列表

好处：改提示词不用改代码（配置与代码解耦）。
"""
import os

import yaml


class BuilderPromptYaml:
    """YAML 提示词构建器"""

    @staticmethod
    def get_prompt(file_name: str) -> str:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, file_name)

        with open(file_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        prompt = (
            f"一 角色:{config['role']}\n"
            f"二 任务:\n{chr(10).join(config['task'])}\n"
            f"三 规则:\n{chr(10).join(config['rule'])}\n"
            f"四 输出:\n{chr(10).join(config['output'])}\n"
            f"五 示例:\n{chr(10).join(config['example'])}\n"
        )
        return prompt.strip()


if __name__ == "__main__":
    b = BuilderPromptYaml()
    print(b.get_prompt("chat_node.yaml"))
