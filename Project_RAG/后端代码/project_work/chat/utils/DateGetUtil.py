# import chromadb
# from langchain_core.documents import Document
# # def data_get(date_info):
# #     """
# #     只根据日期信息检索文档，不进行任何内容过滤或问题检索
# #     :param date_info: 日期信息字典，包含 type 和对应的日期值
# #     :return: List[Document]
# #     """
# #     # 连接数据库
# #     client = chromadb.PersistentClient(path="E:\\daimaxiangmu\\workspace\\project_work\\localchromadb")
# #     collection = client.get_collection("rmtb")
# #
# #     # 1. 根据日期类型构建 where 条件
# #     t = date_info['type']
# #     print("正在进行日期检索")
# #
# #     if t == 'none':
# #         return []
# #     if t == 'single':
# #         where = {"date": date_info['date']}
# #     elif t == 'year':
# #         where = {"year": int(date_info['date'])}
# #     elif t == 'month':
# #         where = {"date": {"$regex": f"^{date_info['date']}"}}
# #     elif t == 'range':
# #         from datetime import datetime, timedelta
# #         start = datetime.strptime(date_info['start'], "%Y-%m-%d")
# #         end = datetime.strptime(date_info['end'], "%Y-%m-%d")
# #         dates = []
# #         d = start
# #         while d <= end:
# #             dates.append(d.strftime("%Y-%m-%d"))
# #             d += timedelta(days=1)
# #         where = {"date": {"$in": dates}}
# #     else:
# #         return []
# #
# #     # 2. 执行查询（只按日期过滤，不加 where_document）
# #     results = collection.get(where=where)
# #
# #     # 3. 转换为 Document 列表
# #     docs = []
# #     print("正在进行转换为 Document 列表")
# #     for i, text in enumerate(results.get('documents', [])):
# #         docs.append(Document(
# #             page_content=text,
# #             id=results['ids'][i],
# #             metadata=results['metadatas'][i] if results.get('metadatas') else {}
# #         ))
# #
# #     print(f"查到 {len(docs)} 条")
# #     return docs
#
#
# #----------------------------------------
# def data_get(date_info):
#     client = chromadb.PersistentClient(path="E:\\daimaxiangmu\\workspace\\project_work\\RebuildChromadb")
#     collection = client.get_collection("rmrb_1")
#
#     t = date_info['type']
#     print("正在进行日期检索")
#
#     if t == 'none':
#         return []
#     if t == 'single':
#         dt = _parse_date(date_info['date'])
#         where = {"date": {"$in": [dt.strftime("%Y-%m-%d"), dt.strftime("%Y/%m/%d")]}}
#     elif t == 'year':
#         where = {"year": int(date_info['date'])}
#     elif t == 'month':
#         from datetime import datetime, timedelta
#         raw = date_info['date']
#         # 尝试解析为年-月
#         try:
#             dt = datetime.strptime(raw, "%Y-%m")
#         except ValueError:
#             dt = datetime.strptime(raw, "%Y/%m")
#         # 计算该月第一天和最后一天
#         first_day = dt.replace(day=1)
#         if dt.month == 12:
#             last_day = dt.replace(year=dt.year + 1, month=1, day=1) - timedelta(days=1)
#         else:
#             last_day = dt.replace(month=dt.month + 1, day=1) - timedelta(days=1)
#         # 生成该月所有日期（两种格式）
#         dates = []
#         d = first_day
#         while d <= last_day:
#             dates.append(d.strftime("%Y-%m-%d"))
#             dates.append(d.strftime("%Y/%m/%d"))
#             d += timedelta(days=1)
#         where = {"date": {"$in": dates}}
#     elif t == 'range':
#         start = _parse_date(date_info['start'])
#         end = _parse_date(date_info['end'])
#         date_values = []
#         d = start
#         while d <= end:
#             date_values.append(d.strftime("%Y-%m-%d"))
#             date_values.append(d.strftime("%Y/%m/%d"))
#             d += timedelta(days=1)
#         where = {"date": {"$in": date_values}}
#     else:
#         return []
#
#     results = collection.get(where=where)
#     docs = []
#     print("正在进行转换为 Document 列表")
#     for i, text in enumerate(results.get('documents', [])):
#         docs.append(Document(
#             page_content=text,
#             id=results['ids'][i],
#             metadata=results['metadatas'][i] if results.get('metadatas') else {}
#         ))
#     print(f"查到 {len(docs)} 条")
#     return docs
#
# from datetime import datetime, timedelta
#
# def _parse_date(date_str):
#     """尝试解析两种格式，返回 datetime 对象"""
#     for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
#         try:
#             return datetime.strptime(date_str, fmt)
#         except ValueError:
#             continue
#     raise ValueError(f"无法解析日期格式: {date_str}")
#
# # if __name__ == '__main__':
# #     date_info = {'type': 'range', 'start': '2025/01/01', 'end': '2025/01/02'}
# #     result=data_get(date_info)
# #     print(result)
#
# #结果长这样：
# # [Document(id='3c444e6b-8919-4bef-b550-9f54ead47919',
# #           metadata={'edition': '第02版：要闻', 'year': 2025, 'source_file': '人民日报2025年文本数据.xlsx', 'title': '在全国政协新年茶话会上的讲话', 'date': '2025/01/01'},
# #           page_content='。改革开放持续深化，胜利召开中共二十届三中全会，对局打牢基础'),
# #  Document(id='a3602f70-3f46-4026-89e7-d877999bc409',
# #           metadata={'year': 2025, 'source_file': '人民日报2025年文本数据.xlsx', 'title': '在全国政协新年茶话会上的讲话', 'date': '2025/01/01', 'edition': '第02版：要闻'},
# #           page_content='。一年来，面对国')]



import chromadb
from langchain_core.documents import Document

def data_get(date_info):
    """
    根据日期信息检索文档（仅支持 single 类型）
    :param date_info: 字典，如 {"type": "single", "date": "2004-02-04"}
    :return: List[Document]
    """
    # 1. 连接数据库（请根据实际情况修改路径和集合名）
    client = chromadb.PersistentClient(path="E:\\daimaxiangmu\\workspace\\project_work\\RebuildChromadb")
    collection = client.get_collection("rmrb_1")

    t = date_info.get('type')
    if t != 'single':
        # 如果不是 single 类型，返回空列表（或根据需求扩展）
        print(f"当前仅支持 single 类型，收到 {t}，返回空结果")
        return []

    # 2. 构建精确日期过滤条件
    date_str = date_info['date']  # 格式应为 YYYY-MM-DD
    where = {"date": date_str}

    # 3. 执行查询
    results = collection.get(where=where)

    # 4. 转换为 Document 列表（保持原有包装格式）
    docs = []
    for i, text in enumerate(results.get('documents', [])):
        docs.append(Document(
            page_content=text,
            id=results['ids'][i],
            metadata=results['metadatas'][i] if results.get('metadatas') else {}
        ))
    print(f"查到 {len(docs)} 条")
    return docs