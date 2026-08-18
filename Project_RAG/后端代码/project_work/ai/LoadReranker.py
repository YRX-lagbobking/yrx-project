from FlagEmbedding import FlagReranker
import dotenv
import os
# 加载重排序模型
def load_reranker():
    return FlagReranker(
    model_name_or_path=os.getenv("RERANKER_MODEL"),
    use_fp16=True
)

