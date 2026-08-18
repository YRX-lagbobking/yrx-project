import chromadb
from langchain_core import documents
from langchain_core.documents import Document
#获取连接对象
client = chromadb.PersistentClient(
    "./localchromadb"
)
#设置创建的集合名称
collection_name="rmtb"
#获取集合对象
collection = client.get_collection(collection_name)
# 精确匹配 date = '2025/01/01'
results = collection.get(
    where={"date": "2025/01/01"}
)
# print(results['documents'])#返回的是所有的块
#--------------------------------------------
# 转为 LangChain Document 列表
langchain_docs = []
for idx, doc_text in enumerate(results['documents']):
    # 每个 doc_text 对应一个块的内容
    # 对应的 metadata 在 results['metadatas'][idx] 里
    langchain_docs.append(
        Document(
            page_content=doc_text,
            metadata=results['metadatas'][idx]
        )
    )
# 现在 langchain_docs 就是你之前 retriever 返回的那种格式
print(langchain_docs)