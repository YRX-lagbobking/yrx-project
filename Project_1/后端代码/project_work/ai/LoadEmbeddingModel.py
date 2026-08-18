from langchain_huggingface import   HuggingFaceEmbeddings
import dotenv
import os
dotenv.load_dotenv()

def load_embedding_model():
    return HuggingFaceEmbeddings(
        model_name=os.getenv("EMBEDDING_MODEL"),
        model_kwargs={
            "device":"cuda",
            "local_files_only":True
        }
    )
