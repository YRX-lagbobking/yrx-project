import json

import os
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
def data_format(question):
    llm = ChatOllama(
        base_url="http://localhost:11434",
        model="qwen2.5:7b",
    )

    # # 使用ChatOpenAI加载千问的模型
    # llm_2 = ChatOpenAI(
    #     # 通过环境变量中读取api-key,我已经在我自己的系统环境变量中配置了
    #     api_key=os.getenv("DASHSCOPE_API_KEY"),
    #     # 指定基础服务地址 --- 阿里公司提供的模型访问地址
    #     base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    #     # 指定模型名称
    #     model="qwen-plus",  # 此处以qwen-plus为例，您可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
    #     # other params...
    #     streaming=True,
    # )
    system_prompt = """
       你是一个日期提取助手。从用户问题中提取日期并转为标准格式。
       例： "2025年1月1日" → {"type":"single","date":"2025-01-01"}
            "2024年" → {"type":"year","date":"2024"}
            "2025年1月" → {"type":"month","date":"2025-01"}
            "23年1月1号到1月3号" → {"type":"range","start":"2023-01-01","end":"2023-01-03"}
            
        1. 如果是一个具体日期：{"type":"single","date":"2020-01-01"}
        2. 如果是日期范围：{"type":"range","start":"2019-01-01","end":"2019-01-03"}
        3. 如果是年份：{"type":"year","date":"2010"}
        4. 如果只有月份：{"type":"month","date":"2010-01"}
        5. 如果没有日期：{"type":"none"}
        
        只输出JSON，不要其他内容。
        
        示例：
        "2010年1月1日" → {"type":"single","date":"2010-01-01"}
        "20年1月1号到1月3号" → {"type":"range","start":"2020-01-01","end":"2020-01-03"}
        "2020年" → {"type":"year","date":"2020"}
        "2019年1月" → {"type":"month","date":"2019-01"}
        "你好" → {"type":"none"}
    """
    rs = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ])
    # 注意这不是一个字典而是一个字符串!所以要转成字典
    return json.loads(rs.content)

if __name__ == '__main__':
    print(data_format('05年1月8号是我的生日，那天发生了什么'))
    print(data_format('25年1月8号有啥新闻'))

