import pandas as pd
from langchain_core.documents import Document
import glob#为了读取所有的文件
import os
file_path=r"E:\daimaxiangmu\workspace\project_work\datasets\分年份保存数据"
# 2. 使用glob匹配所有Excel文件
# 匹配所有.xlsx文件，如果想匹配特定模式如"人民日报*.xlsx"可以用 '人民日报*.xlsx'
excel_files = glob.glob(os.path.join(file_path, '*.xlsx'))


def make_document():
    documents = []
    # 3. 遍历每个文件
    for file_path in excel_files:
        print(f"正在处理: {os.path.basename(file_path)}")

        # 读取当前文件
        df = pd.read_excel(file_path)

        # 处理当前文件的所有行
        for _, row in df.iterrows():
            page_content = f"{row['日期'].replace('/', '-')} {row['标题']} {row['文本内容']}"

            metadata = {
                "year": row["年份"],
                "date": row['日期'].replace('/', '-'),
                "edition": row["报纸版次"],
                "title": row["标题"],
                "source_file": os.path.basename(file_path)  # 可选：记录来源文件
            }

            doc = Document(page_content=page_content, metadata=metadata)
            documents.append(doc)

    print(f"总共读取了 {len(excel_files)} 个文件")
    print(f"生成了 {len(documents)} 条文档")
    print(f"示例文档: {documents[:2]}")

    return documents

if __name__ == '__main__':
    make_document()
    #示例文档: [Document(metadata={'year': 2025, 'date': '2025/01/01', 'edition': '第01版：要闻', 'title': '中俄两国元首互致新年贺电', 'source_file': '人民日报2025年文本数据.xlsx'}, page_content='2025/01/01 中俄两国元首互致新年贺电
    #示例文档： [Document(metadata={'year': 2005.0, 'date': '2005-01-01', 'edition': nan, 'title': '赤壁鱼糕多味斋', 'source_file': '人民日报2005年文本数据.xlsx'}, page_content='2005-01-01 赤壁鱼糕多味斋']