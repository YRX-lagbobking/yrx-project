"""
记忆组装层（manager）

    session_mananger.py 会话管理器：一次对话里四层记忆对象的统一入口（注意文件名沿用 exam_0914 的拼写）
    prompt_builder.py   提示词组装：把 L1~L4 拼成中文 system 提示词
    memory_manager.py   记忆更新流水线：对话结束后更新 L2/L3/L4
"""
