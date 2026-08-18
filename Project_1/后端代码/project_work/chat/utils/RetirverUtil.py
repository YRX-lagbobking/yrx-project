
# vector_result=RetirverUtil.retriver_fc(date,question,chroma_db_retriever,chromadb_2)
'''
有三种日期方式：1{'date': '2005-01-01'}。放弃三种日期方式...

'''
def retriver_fc(date,question,chromadb_1):
    result=chromadb_1.similarity_search(
        question,
        k=10,
        filter={"date":date['date']})
    return result