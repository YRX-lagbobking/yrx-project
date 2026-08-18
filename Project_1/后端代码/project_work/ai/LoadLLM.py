from langchain_openai import ChatOpenAI
import os
def load_llm():
    return ChatOpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.7-plus",
        streaming=True,
        temperature=0.7
    )