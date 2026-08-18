from langchain_core.output_parsers import StrOutputParser
from fastapi import Request
from ai import LoadEmbeddingModel, LoadChroma, LoadLLM, LoadReranker
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableParallel, RunnablePassthrough, RunnableLambda
import dotenv
import os
from chat.utils import IntentionUtil, DateGetUtil, DateFormatUtil, BM25Util, RRFUtil,RetirverUtil
from chat.service import HistoryService

dotenv.load_dotenv()

def chat(request:Request,chatclass):
    models=request.app.state.models
    llm=models.llm
    reranker=models.reranker
    chroma_db_retriever = models.vector
    chroma_db_retriever_2 = models.vector_2
    chromadb_1=models.chromadb_1
    chromadb_2=models.chromadb_2
    #-----------------------------
    question=chatclass.question
    history_id=chatclass.history_id
    if history_id==0:
        history=[]
    else:
        # # 查询对话记录---没有考虑只查询最近几条记录的值
        # history = HistoryService.conversation_log(history_id)['data']
        history=HistoryService.history_compress(history_id)
        #这个历史记录是一个列表形式，最开始是压缩的文本
    """
        使用一个小模型来进行意图识别
        结果：{'intent': 'news', 'confidence': 'high'}
    意图类别
    1. **date_event**：用户询问某个具体日期（如“2024年1月1日”）发生了什么事情，或者询问某一天发生过的事件（如“昨天有什么大事”、“国庆节那天发生了什么”）。
    2. **news**：用户询问新闻、消息、报道、热搜等当前或近期的时事信息（如“最近有什么科技新闻”、“关于AI的新闻”、“今天的热搜是什么”）。
    3. **other**：不属于以上两类（如闲聊、技术问题、生活咨询、法律咨询等）。
        """
    intention_result=IntentionUtil.intention_recognition(question)
    intention=intention_result['intent']
    if intention == 'other':
        print("走other\n\n")
        #不走RAG；
        history.append({"role":"user","content":question})
        for chunk in llm.stream(history):
            if chunk.content:
                yield chunk.content
        return
    #--------------------------------------------------------------------------------------------------
    elif intention == 'date_event':
        print("走date_event\n\n")
        #需要取进行元数据处理，从元数据中获取日期信息，这里可以把问题拿进去
        date=DateFormatUtil.data_format(question)
        context=DateGetUtil.data_get(date)
        # 提示词
        template = """
                   你是一个基于知识库的AI助手。请根据RAG检索内容回答用户问题。
                       规则：
                           - 基于参考资料，尽可能提供丰富、具体的信息（包括时间、地点、数据、事例等）。
                           - 回答要有细节，不要只给一句话概括，要展开说明。
                           - 如果参考资料中有多个相关事件，请分别列出。
                           - 回答长度控制在 150~300 字左右（视信息量而定）。
                           - 仅基于提供的知识回答，不使用外部知识补充,。
                           - 检索内容不足时，说明信息不足，不要猜测。
                           - 标题优先提炼关键答案，避免冗长解释,
                           - 保持回答自然、简洁、有帮助。
                           - 输出结果的时候，不允许输出根据提供的参考资料这样的内容,
                           - 输出结果的时候，如果没有参考的上下文信息，请给出一个友好的回复信息,
                           -如果用户的问题跟生日相关，跟节日相关，请给出一个友好的回复信息,并围绕提取得到的信息进行生日祝福。
                       历史记录：
                       {history}
                       参考资料:
                       {context}
                       问题:
                       {question}
                       回答:
                   """
        # 创建提示词对象PromptTemplate
        prompt = PromptTemplate(
            template=template,
            input_variables=["history", "context", "question"]
        )
        # # 创建检索器对象retriver:
        # chroma_db = LoadChroma.load_chroma()
        # vector_retriever = chroma_db.as_retriever(search_kwargs={"k": 15})
        def retriver_fc():
            vector_result=RetirverUtil.retriver_fc(date,question,chromadb_1)
            print("向量数据库检索结果：", vector_result)
            bm25, docs = BM25Util.get_bm25(context)
            # bm25检索结果
            bm25_result = BM25Util.bm25_search(bm25, docs, question, k=15)
            # 利用倒数排名RRF融合
            result = RRFUtil.rrf(vector_result, bm25_result)
            return result

        def reranker_fc(data):
            history = data['history']
            question = data["question"]
            context = data["context"]  # 等于retriver里的document对象
            print("context", context)
            rerank_list = [(item.page_content, question) for item in context]
            scores = reranker.compute_score(rerank_list)
            print(f"重排序得分：{scores}")
            print(f"===============")
            scores = [{index: item} for index, item in enumerate(scores)]
            print(f"重排序字典格式{scores}")
            print(f"===============")
            scores.sort(key=lambda x: list(x.values())[0], reverse=True)  # 对计算结果进行排序
            print(f"对结果进行排序{scores}")
            print(f"===============")
            top_k = 3
            context_re = []
            for item in scores[:top_k]:
                for one in item.keys():
                    context_re.append(context[one].page_content)
            result = {
                "history": history,
                "context": context_re,
                "question": question
            }
            print(f"重排序结果context_re：{context_re}")
            return result

        # 创建langchian链并行执行器Runnable
        qa_chain = (
                RunnableParallel(
                    {
                        "context": RunnableLambda(lambda _: retriver_fc()),
                        "history": RunnableLambda(lambda _: history),
                        "question": RunnablePassthrough()
                    })
                | RunnableLambda(reranker_fc)
                | prompt
                | llm
                | StrOutputParser()
        )
        for chunk in qa_chain.stream(question):
            if chunk:
                print(chunk)
                yield chunk
    #-------------------------------------------------------------------------------------------------
    elif intention == 'news':
            print("走news\n\n")
            #提示词
            template="""
            你是一个基于知识库的AI助手。请根据RAG检索内容回答用户问题。
                规则：
                   - 基于参考资料，尽可能提供丰富、具体的信息（包括时间、地点、数据、事例等）。
                   - 回答要有细节，不要只给一句话概括，要展开说明。
                   - 如果参考资料中有多个相关事件，请分别列出。
                   - 回答长度控制在 150~300 字左右（视信息量而定）。
                   - 仅基于提供的知识回答，不使用外部知识补充,。
                   - 检索内容不足时，说明信息不足，不要猜测。
                   - 标题优先提炼关键答案，避免冗长解释,
                   - 保持回答自然、简洁、有帮助。
                   - 输出结果的时候，不允许输出根据提供的参考资料这样的内容,
                   - 输出结果的时候，如果没有参考的上下文信息，请给出一个友好的回复信息,
                   -如果用户的问题跟生日相关，跟节日相关，请给出一个友好的回复信息,并围绕提取得到的信息进行生日祝福。
                历史记录：
                {history}
                参考资料:
                {context}
                问题:
                {question}
                回答:
            """
            # 创建提示词对象PromptTemplate
            prompt = PromptTemplate(
                template=template,
                input_variables=["history", "context", "question"]
            )
            # # #创建检索器对象retriver:
            # chroma_db=LoadChroma.load_chroma_2()
            # vector_retriever = chroma_db.as_retriever(search_kwargs={"k":10})
            def retriver_fc():
                vector_result = chroma_db_retriever_2.invoke(question)
                print("news向量检索结果：",vector_result)
                bm25,docs=BM25Util.get_bm25(chromadb_2)
                # bm25检索结果
                bm25_result = BM25Util.bm25_search(bm25, docs, question, k=10)
                print("=======================")
                print("news,bm25检索结果：",bm25_result)
                # 利用重排序算法RRF融合
                result = RRFUtil.rrf(vector_result, bm25_result)
                return result

            def reranker_fc(data):
                history=data['history']
                question = data["question"]
                context = data["context"]  # 等于retriver里的document对象
                print("context", context)
                rerank_list = [(item.page_content, question) for item in context]
                scores = reranker.compute_score(rerank_list)
                print(f"重排序得分：{scores}")
                print(f"===============")
                scores = [{index: item} for index, item in enumerate(scores)]
                print(f"重排序字典格式{scores}")
                print(f"===============")
                scores.sort(key=lambda x: list(x.values())[0], reverse=True)  # 对计算结果进行排序
                print(f"对结果进行排序{scores}")
                print(f"===============")
                top_k = 3
                context_re = []
                for item in scores[:top_k]:
                    for one in item.keys():
                        context_re.append(context[one].page_content)
                result = {
                    "history": history,
                    "context": context_re,
                    "question": question
                }
                print(f"重排序结果context_re：{context_re}")
                return result
            # 创建langchian链并行执行器Runnable
            qa_chain = (
                    RunnableParallel(
                        {
                            "context": RunnableLambda(lambda _: retriver_fc()),
                            "history": RunnableLambda(lambda _: history),
                            "question": RunnablePassthrough()
                        })
                    | RunnableLambda(reranker_fc)
                    | prompt
                    | llm
                    | StrOutputParser()
            )
            for chunk in qa_chain.stream(question):
                if chunk:
                    print(chunk)
                    yield chunk
from chat.entity.SaveChatEntity import SaveChatClass
from chat.dao import SaveChatDao
def save_chat(savechatentity:SaveChatClass):
    user_id=savechatentity.user_id
    parent_id=savechatentity.parent_id
    question=savechatentity.question
    answer=savechatentity.answer
    history_id=SaveChatDao.save_chat(question,parent_id,user_id,answer)
    if history_id==0:
            return {
            "code": 500,
            "message": "保存失败",
            "data": None
                }
    return{
        "code": 200,
        "message": "保存成功",
        "data": history_id
    }

# if __name__ == '__main__':
#     class ChatClass:
#         history_id=0
#         question="25年1月1号有什么新闻吗"
#
#     # 遍历生成器，逐个获取 yield 的 chunk
#     print("回答")
#     for chunk in chat(ChatClass):
#         print(chunk)

