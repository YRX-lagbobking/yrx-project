import json

import os
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI


def intention_recognition(question):
    # llm = ChatOllama(
    #     base_url="http://localhost:11434",
    #     model="qwen2.5:7b",
    # )

    # 使用ChatOpenAI加载千问的模型
    llm_2 = ChatOpenAI(
        # 通过环境变量中读取api-key,我已经在我自己的系统环境变量中配置了
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        # 指定基础服务地址 --- 阿里公司提供的模型访问地址
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        # 指定模型名称
        model="qwen-plus",  # 此处以qwen-plus为例，您可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
        # other params...
        streaming=True,
    )

    system_prompt = """
        你是一个专业的意图识别专家。你的任务是判断用户的输入属于以下哪种意图。
        # 意图类别
        1. **date_event**：用户询问某个具体日期（如“2024年1月1日”）发生了什么事情，那一天有什么新闻或者询问某一天发生过的事件（如“昨天有什么大事”、“国庆节那天发生了什么”），某一天是生日、节假日、特殊事件。
        2. **news**：用户询问新闻、消息、报道、人民日报、资料、报纸等当前或近期的时事信息（如“最近有什么科技新闻”、“关于AI的新闻”、“报纸里的内容”）。
        3. **other**：不属于以上两类（如闲聊、技术问题、生活咨询、法律咨询等）。
        # 判断规则
        - 优先根据关键词判断：
          - 如果同时出现日期词（如“2024年”、“昨天”、“明天”、“国庆节”、“上周五”）和“发生”、“事件”、“大事”等查询事件的词汇 → 归类为 **date_event**。
          - 如果出现“新闻”、“消息”、“报道”、“热搜”、“时事”、“报纸”、“人民日报”等词 → 归类为 **news**。
        - 如果都不匹配，归类为 **other**。
        # 输出格式
        仅输出一个JSON对象，不要包含任何其他解释文字：
        {
            "intent": "date_event" / "news" / "other",
            "confidence": "high" / "medium" / "low"
        }
        
        # Examples
        User: "2024年1月1日发生了什么大事"
        Assistant: {"intent": "date_event", "confidence": "high"}
        User: "25年1月1号有什么外交新闻吗"
        Assistant: {"intent": "date_event", "confidence": "high"}
        User: "2024年1月1日的新闻"  
        Assistant: {"intent": "date_event", "confidence": "high"}  
        User: "2020年的疫情怎么样"  
        Assistant: {"intent": "date_event", "confidence": "high"}  
        User: "昨天有没有什么重要事件"
        Assistant: {"intent": "date_event", "confidence": "high"}
        
        User: "今天有什么新闻"
        Assistant: {"intent": "date_event", "confidence": "high"}
        
        User: "最近关于人工智能的新闻有哪些"
        Assistant: {"intent": "news", "confidence": "high"}
        
        User: "国庆节那天发生了什么事"  
        Assistant: {"intent": "date_event", "confidence": "high"}
        
        User: "Python怎么读取Excel文件"
        Assistant: {"intent": "other", "confidence": "high"}
        
        User: "我和房东签了合同但他不退押金怎么办"
        Assistant: {"intent": "other", "confidence": "high"}
        
        User: "今天天气怎么样"  
        Assistant: {"intent": "other", "confidence": "high"}  # 不是事件也不是新闻

    """
    rs = llm_2.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ])

    # 注意这不是一个字典而是一个字符串!所以要转成字典
    return json.loads(rs.content)


if __name__ == '__main__':
    print(intention_recognition("2005年1月8号是我的生日"))
#     print(type(intention_recognition("23年1月发生了什么新闻")))
