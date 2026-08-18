from ai import LoadChroma

chroma_db = LoadChroma.load_chroma()
vector_retriever = chroma_db.as_retriever(search_kwargs={"k": 10})

db=LoadChroma.load_chroma()
results = db.similarity_search(
    query="社会",
    k=5,
    filter={'date': '2026-01-01'}   # 只返回 metadata.time == "2026" 的文档
)
print(results)#<class 'str'>
# 打印集合中的文档数量
print(f"集合中的文档数: {db._collection.count()}")