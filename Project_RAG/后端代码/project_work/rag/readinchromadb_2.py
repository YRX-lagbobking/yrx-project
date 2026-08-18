from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from ai import LoadEmbeddingModel, LoadChroma
from rag import makedocument
import dotenv
import os
dotenv.load_dotenv()

embedding = LoadEmbeddingModel.load_embedding_model()
chroma_client = LoadChroma.load_chroma_2()

documents = makedocument.make_document()#5

# 文本切割：只切 page_content，每个小块自动保留原来的 metadata
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
    separators=[
        "。", "，", "？", "、", "：", "；", "！", "“", "”", "（",
        "）", "《", "》", "……", "——", "‘", "’", "·", "【", "】",
    ],
    length_function=len,
)
documents = text_splitter.split_documents(documents)
print("------------------")
for chunk in documents[:3]:
        print("chunk",chunk)
#存入数据
try:
        print("正在进行数据存储")
        Chroma.from_documents(
                #存入数据文本
                documents=documents,
                #向量化模型
                embedding=embedding,
                #存储的位置
                persist_directory=r"E:\daimaxiangmu\workspace\project_work\NewChromadb",
                #检索的方式
                collection_metadata={
                        "hnsw:space": 'cosine'
                },
                #集合名称
                collection_name="rmrb_2"

        )
        print("数据存储成功")
except Exception as e:
        print("数据存储失败",e)

