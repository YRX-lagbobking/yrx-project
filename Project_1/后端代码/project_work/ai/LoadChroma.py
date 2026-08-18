from langchain_chroma import Chroma
from ai import LoadEmbeddingModel
import os
import dotenv
dotenv.load_dotenv()
def load_chroma():
    return Chroma(#5
        collection_name="rmrb_1",
        embedding_function=LoadEmbeddingModel.load_embedding_model(),
        persist_directory="E:\\daimaxiangmu\\workspace\\project_work\\RebuildChromadb"
    )
def load_chroma_2():
    return Chroma(#5
        collection_name="rmrb_2",
        embedding_function=LoadEmbeddingModel.load_embedding_model(),
        persist_directory="E:\\daimaxiangmu\\workspace\\project_work\\NewChromadb"
    )
