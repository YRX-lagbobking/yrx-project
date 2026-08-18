from langchain_core.documents import Document
from rank_bm25 import BM25Okapi
import jieba

STOP_WORDS = [
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都",
    "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会",
    "着", "没有", "看", "好", "自己", "这", "那", "他", "她", "它", "们",
    "这个", "那个", "什么", "哪", "怎么", "吗", "呢", "吧", "啊", "哦",
    "还", "被", "把", "让", "从", "对", "与", "但", "而", "或", "所",
    "为", "以", "及", "可", "可以", "能", "能够", "应该", "需要", "已经",
    "虽然", "如果", "因为", "所以", "只是", "还是", "不过", "然后",
    "之", "其", "中", "等", "等等", "即", "使", "向", "将", "按", "当",
    "于", "由", "比", "除了", "关于", "以及", "并且", "此外", "另外",
    "过", "着", "来", "去", "做", "作", "像", "如", "如同", "由于",
]
#分词函数
def tokenize(text):
    return [token for token in jieba.cut(text) if token.strip() not in STOP_WORDS and len(token.strip())>=1]
#要创建BM25对象和提取出文档内容
def get_bm25(chroma_db):
     if isinstance(chroma_db, list):
        documents=chroma_db
        contents = [doc.page_content for doc in documents]

     else:
        data=chroma_db.get()#这个结果是字典！
        ids=data['ids']
        contents=data['documents']#我们需要的文档
        metadata=data['metadatas']
        documents = [Document(page_content=contents[i], id=ids[i], metadata=metadata[i]) for i in range(len(ids))]
     #分词
     doc_tokenized = [tokenize(doc) for doc in contents]
     #创建BM25对象
     bm25=BM25Okapi(doc_tokenized)
     return bm25,documents
#------------------
# ===== 从 List[Document] 构建 BM25 =====
def get_bm25_from_docs(docs_list):
    """
    从 List[Document] 构建 BM25 对象，保留完整元数据
    :param docs_list: List[Document]
    :return: bm25, docs (原始 docs 列表，保留所有元数据)
    """
    if not docs_list:
        return None, []

    # 提取 page_content
    doc_texts = [doc.page_content for doc in docs_list]

    # 分词
    doc_tokenized = [tokenize(text) for text in doc_texts]

    # 创建 BM25 对象
    bm25 = BM25Okapi(doc_tokenized)

    # 直接返回原始 docs（包含完整 metadata），不需要重新构造
    return bm25, docs_list

#_----------------
#BM25检索
def bm25_search(bm25, docs, question, k):
    #bm25得分
    scores=bm25.get_scores(tokenize(question))#结果为一个得分列表
    #排序
    # 对得分进行排序，获取索引
    result_sort=sorted(range(len(scores)) ,key=lambda i:scores[i],reverse=True)[:k]
    #包装结果
    result=[docs[i] for i in result_sort]
    #返回的结果应该与向量检索包装得一样
    return result