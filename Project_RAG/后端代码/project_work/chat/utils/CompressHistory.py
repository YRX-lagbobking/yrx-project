import json
from langchain_openai import ChatOpenAI
import os



def compress_HISTORY(records: list)->str:

    # 使用ChatOpenAI加载千问的模型
    llm = ChatOpenAI(
        # 通过环境变量中读取api-key,我已经在我自己的系统环境变量中配置了
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        # 指定基础服务地址 --- 阿里公司提供的模型访问地址
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        # 指定模型名称
        model="qwen-plus",  # 此处以qwen-plus为例，您可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
    )
    conversation_text = "\n".join(
        [f"第{i}轮：用户问「{rec['question']}」，助手答「{rec['answer']}」"
         for i, rec in enumerate(records, 1)]
    )
    system_prompt = f"""
        你是一个对话摘要专家。我将给你提供一系列用户与助手的对话记录，每一条记录包含“用户的问题”和“助手的回答”。你需要将这些对话压缩成一段简洁、连贯的摘要，用于后续对话的上下文参考。
        我传给你的格式为 JSON 列表。
        每条记录中的 "question" 是用户的问题，"answer" 是助手的回答。
        请忽略 "history_id"、"username"、"parent_id"、"create_time" 这些字段，只关注问答内容。

        【压缩要求】
        1. 按对话发生的先后顺序，提炼出核心话题、用户的主要诉求、助手给出的关键信息或解决方案。
        2. 保留重要的细节（如具体数字、时间、事件名称、决策点），省略寒暄、重复和无关的客套话。
        3. 如果对话中存在话题切换或转折，请在摘要中清晰体现。
        4. 摘要需保持逻辑顺畅，语言自然，总字数严格控制在 150 字以内。
        5. 只输出摘要文本，不要添加任何额外说明、序号或标题。
        
        【对话记录】
        {conversation_text}
        
        请输出压缩后的摘要：
    """
    rs=llm.invoke(system_prompt)
    # 注意这不是一个字典而是一个字符串!所以要转成字典
    rs=rs.content.strip()
    return rs

