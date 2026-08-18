
import chromadb
from langchain_core.documents import Document
def data_get(date_info):
    """
    只根据日期信息检索文档，不进行任何内容过滤或问题检索
    :param date_info: 日期信息字典，包含 type 和对应的日期值
    :return: List[Document]
    """
    # 连接数据库
    client = chromadb.PersistentClient(path="E:\\daimaxiangmu\\workspace\\project_work\\NewChromadb")
    collection = client.get_collection("rmrb")

    # 1. 根据日期类型构建 where 条件
    t = date_info['type']
    print("正在进行日期检索")

    if t == 'none':
        return []
    if t == 'single':
        where = {"date": date_info['date']}
    elif t == 'year':
        where = {"year": int(date_info['date'])}
    elif t == 'month':
        where = {"date": {"$regex": f"^{date_info['date']}"}}
    elif t == 'range':
        from datetime import datetime, timedelta
        start = datetime.strptime(date_info['start'], "%Y-%m-%d")
        end = datetime.strptime(date_info['end'], "%Y-%m-%d")
        dates = []
        d = start
        while d <= end:
            dates.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        where = {"date": {"$in": dates}}
    else:
        return []

    # 2. 执行查询（只按日期过滤，不加 where_document）
    results = collection.get(where=where)

    # 3. 转换为 Document 列表
    docs = []
    print("正在进行转换为 Document 列表")
    for i, text in enumerate(results.get('documents', [])):
        docs.append(Document(
            page_content=text,
            id=results['ids'][i],
            metadata=results['metadatas'][i] if results.get('metadatas') else {}
        ))

    print(f"查到 {len(docs)} 条")
    return docs

if __name__ == '__main__':
    date_info = {'type': 'single', 'date': '2005-01-01'}
    result=data_get(date_info)
    print(result)

#结果长这样：
# [Document(id='3c444e6b-8919-4bef-b550-9f54ead47919',
#           metadata={'edition': '第02版：要闻', 'year': 2025, 'source_file': '人民日报2025年文本数据.xlsx', 'title': '在全国政协新年茶话会上的讲话', 'date': '2025/01/01'},
#           page_content='。改革开放持续深化，胜利召开中共二十届三中全会，对局打牢基础'),
#  Document(id='a3602f70-3f46-4026-89e7-d877999bc409',
#           metadata={'year': 2025, 'source_file': '人民日报2025年文本数据.xlsx', 'title': '在全国政协新年茶话会上的讲话', 'date': '2025/01/01', 'edition': '第02版：要闻'},
#           page_content='。一年来，面对国')]