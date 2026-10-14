"""
基于单例模式的模型封装

包含 6 类模型对象（全部懒加载）：
    1. 主模型（云端，如 qwen3-max）
    2. 备用模型（另一家云厂商，降级第 1 级）
    3. 本地模型（Ollama，降级第 2 级）
    4. Vosk 语音识别模型
    5. Embedding 向量模型（RAG 与长期记忆共用）
    6. Rerank 模型名（精排，实际调用在 rag_tool 内）

设计要点：
    - 单例：避免每个节点重复创建模型对象（重复建连、重复读配置）；
    - 可选依赖延迟导入：vosk / langchain_openai 在函数内部导入，
      未安装时只影响对应能力，不影响项目导入；
    - 降级：get_text_by_fallback() 提供"主 → 备用 → 本地 → 静态文案"三级降级。
"""
from dotenv import load_dotenv
from app.core.config import settings
from app.core.logger import logger

# 读取配置文件
load_dotenv()

# 全部模型失败时的静态兜底文案
STATIC_FALLBACK_TEXT = (
    "抱歉，智能助手暂时有点忙，请稍后再试。\n"
    "如果您身体不舒服，请立即联系家属，紧急情况请拨打 120。"
)


class MyModel:
    """模型工厂（单例）"""

    # 定义私有属性
    _model = None            # 主模型
    _backup_model = None     # 备用模型
    _local_model = None      # 本地模型
    _vosk_model = None       # 语音识别模型
    _embedding_model = None  # 向量模型

    # ---------------- 主模型（云端） ----------------
    @staticmethod
    def get_model():
        """创建/复用在线主模型（流式输出）"""
        if MyModel._model is None:
            from langchain_openai import ChatOpenAI

            MyModel._model = ChatOpenAI(
                model=settings.model_line_name,
                api_key=settings.dashscope_api_key or "empty",
                base_url=settings.openai_api_base,
                streaming=True,          # 开启流式输出
                extra_body={
                    "enable_thinking": False  # 是否开启思考过程
                },
            )
        return MyModel._model

    # ---------------- 备用模型（降级第 1 级） ----------------
    @staticmethod
    def get_backup_model():
        """创建/复用备用云端模型"""
        if MyModel._backup_model is None:
            from langchain_openai import ChatOpenAI

            MyModel._backup_model = ChatOpenAI(
                model=settings.model_backup_name,
                api_key=settings.backup_api_key or "empty",
                base_url=settings.backup_base_url,
                streaming=True,
                extra_body={"enable_thinking": False},
            )
        return MyModel._backup_model

    # ---------------- 本地模型（降级第 2 级） ----------------
    @staticmethod
    def get_local_model():
        """创建/复用本地 Ollama 模型"""
        if MyModel._local_model is None:
            from langchain_openai import ChatOpenAI

            MyModel._local_model = ChatOpenAI(
                model=settings.model_local_name,
                api_key="ollama",
                base_url=settings.local_url,
                streaming=True,          # 开启流式输出
                extra_body={
                    "enable_thinking": False  # 是否开启思考过程
                },
            )
        return MyModel._local_model

    # ---------------- 语音识别模型 ----------------
    @staticmethod
    def get_vosk_model():
        """加载 Vosk 离线语音模型（延迟导入 vosk，未安装时返回 None）"""
        if MyModel._vosk_model is None:
            try:
                from vosk import Model
            except ImportError:
                logger.warning("未安装 vosk，语音识别能力不可用")
                return None
            model_path = settings.vosk_path
            if not model_path:
                logger.warning("VOSK_PATH 未配置，语音识别能力不可用")
                return None
            # 加载模型（声学模型 + 语言模型）
            MyModel._vosk_model = Model(model_path=model_path)
        return MyModel._vosk_model

    # ---------------- 向量模型（RAG / 长期记忆共用） ----------------
    @staticmethod
    def get_embedding_model():
        """创建/复用 Embedding 模型：本地 HuggingFace / Ollama / 云端 DashScope 三选一"""
        if MyModel._embedding_model is None:
            provider = (settings.embedding_provider or "dashscope").lower()

            if provider in ("huggingface", "hf", "local"):
                # 本地模型：直接读磁盘目录，不联网、不下载
                from langchain_huggingface import HuggingFaceEmbeddings

                MyModel._embedding_model = HuggingFaceEmbeddings(
                    model_name=settings.embedding_model_path,  # D:\Model\bge-base-zh-v1.5
                    model_kwargs={"device": settings.embedding_device},  # cpu / cuda
                    encode_kwargs={"normalize_embeddings": True},  # BGE 必须归一化
                )
            elif provider == "ollama":
                from langchain_openai import OpenAIEmbeddings

                MyModel._embedding_model = OpenAIEmbeddings(
                    model=settings.embedding_model,
                    api_key="ollama",
                    base_url=settings.local_url,
                    check_embedding_ctx_length=False,  # Ollama 不支持 tiktoken 分批
                )
            else:
                from langchain_openai import OpenAIEmbeddings

                MyModel._embedding_model = OpenAIEmbeddings(
                    model=settings.embedding_model,
                    api_key=settings.dashscope_api_key or "empty",
                    base_url=settings.openai_api_base,
                )
        return MyModel._embedding_model

    # ---------------- 精排模型名 ----------------
    @staticmethod
    def get_rerank_model() -> str:
        """返回 Rerank 精排模型名（真实调用在 rag_tool 内部完成）"""
        return settings.rerank_model

    # ---------------- 三级降级取文本（同步） ----------------
    @staticmethod
    def get_text_by_fallback(prompt: str) -> str:
        """
        同步版三级降级调用：主模型 → 备用模型 → 本地模型 → 静态文案。
        用于非流式、需要"一定能拿到结果"的场景（如后台任务文案生成）。
        """
        candidates = [
            ("primary", MyModel.get_model),
            ("backup", MyModel.get_backup_model),
            ("local", MyModel.get_local_model),
        ]
        for name, factory in candidates:
            try:
                model = factory()
                result = model.invoke(prompt)
                if name != "primary":
                    logger.warning(f"模型已降级到 {name}")
                return getattr(result, "content", str(result))
            except Exception as e:  # 单级失败不影响整体
                logger.error(f"模型 {name} 调用失败：{e}")
        # 全部失败 → 静态兜底
        logger.critical("所有模型均不可用，返回静态兜底文案")
        return STATIC_FALLBACK_TEXT


def model_fallback_middleware():
    """
    模型降级中间件：主模型调用失败 → 备用模型 → 本地模型。

    用法（节点里）：
        agent = create_agent(model=MyModel.get_model(),
                             middleware=model_fallback_middleware(), ...)

    为什么传"模型对象"而不是模型名字符串：
        本项目用的是自定义 base_url 的 OpenAI 兼容端点（dashscope / 百炼网关 / 本地 Ollama），
        传字符串会新建默认 client、可能连到错误地址；传 MyModel 造好的对象最稳。
    为什么用中间件（而不是把 model 换成 with_fallbacks 的 Runnable）：
        ModelFallbackMiddleware 运行在 create_agent 内部的"模型调用"层，
        因此 tools / system_prompt / response_format / astream 流式全部透明。
    容错：langchain 版本过旧没有这个类时返回 []（等于不降级），绝不影响对话。
    """
    try:
        from langchain.agents.middleware import ModelFallbackMiddleware      # 延迟导入
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ModelFallbackMiddleware 不可用（langchain 版本过旧？），本次不启用模型降级：{e}")
        return []
    return [ModelFallbackMiddleware(MyModel.get_backup_model(), MyModel.get_local_model())]


if __name__ == "__main__":
    # 演示：本地模型可用时打印一句话，不可用时走降级
    print(MyModel.get_text_by_fallback("你好，请用一句话介绍你自己"))
