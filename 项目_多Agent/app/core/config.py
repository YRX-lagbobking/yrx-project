"""
全局配置单例（唯一配置事实来源）

设计说明：
    1. 所有模块统一 `from app.core.config import settings`，禁止在业务代码里散落 os.getenv；
    2. 缺失配置一律给"安全的开发态默认值"，保证没有 .env 时项目也能导入、能降级运行；
    3. 敏感信息（Token / 密码 / 授权码）只存在于 .env，不进代码、不进 Git。
"""
import os
from dotenv import load_dotenv

# 读取项目根目录下的 .env
load_dotenv()


def _get(key: str, default: str = "") -> str:
    """读取环境变量，去除首尾空白"""
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip()


def _get_int(key: str, default: int) -> int:
    """读取整型环境变量，非法值回退默认值"""
    try:
        return int(_get(key, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _get_float(key: str, default: float) -> float:
    """读取浮点环境变量，非法值回退默认值"""
    try:
        return float(_get(key, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _get_bool(key: str, default: bool) -> bool:
    """读取布尔环境变量（true/1/yes/on 视为 True）"""
    value = _get(key, "true" if default else "false").lower()
    return value in ("true", "1", "yes", "on")


class Settings:
    """项目配置对象（单例，模块底部实例化）"""

    def __init__(self):
        # ---------------- 大模型 ----------------
        self.model_line_name = _get("MODEL_LINE_NAME", "qwen3-max")
        self.dashscope_api_key = _get("DASHSCOPE_API_KEY", "")
        self.openai_api_base = _get("OPENAI_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        # 备用模型（降级第 1 级）
        self.model_backup_name = _get("MODEL_BACKUP_NAME", "qwen3.7-max")
        self.backup_api_key = _get("BACKUP_API_KEY", "")
        self.backup_base_url = _get("BACKUP_BASE_URL", "https://ws-2olspiqrz2b6qq78.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        # 本地模型（降级第 2 级，Ollama）
        self.model_local_name = _get("MODEL_LOCAL_NAME", "qwen2.5:3b")
        self.local_url = _get("LOCAL_URL", "http://localhost:11434/v1")

        # ---------------- MySQL（业务结构化数据） ----------------
        self.db_host = _get("DB_HOST", "localhost")
        self.db_port = _get("DB_PORT", "3306")
        self.db_user = _get("DB_USER", "root")
        self.db_password = _get("DB_PASSWORD", "1234")
        self.db_name = _get("DB_NAME", "health_butler")

        # ---------------- PostgreSQL（摘要记忆 + Checkpointer） ----------------
        self.pg_host = _get("PG_HOST", "127.0.0.1")
        self.pg_port = _get("PG_PORT", "5432")
        self.pg_db = _get("PG_DB", "postgres")
        self.pg_user = _get("PG_USER", "postgres")
        self.pg_password = _get("PG_PASSWORD", "1234")
        self.postgresql_url = _get(
            "POSTGRESQL_URL",
            f"postgresql://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_db}?sslmode=disable",
        )
        # 会话状态存储方式：postgres（生产）/ memory（开发调试）
        self.checkpointer = _get("CHECKPOINTER", "postgres").lower()

        # ---------------- Redis（窗口记忆 / 画像 / 会话 / 锁） ----------------
        self.redis_host = _get("REDIS_HOST", "localhost")
        self.redis_port = _get_int("REDIS_PORT", 6379)
        self.redis_db = _get_int("REDIS_DB", 0)
        self.redis_password = _get("REDIS_PASSWORD", "")
        self.redis_url = _get("REDIS_URL", f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}")

        # ---------------- Milvus（知识库向量 + 长期记忆） ----------------
        self.milvus_host = _get("MILVUS_HOST", "localhost")
        self.milvus_port = _get("MILVUS_PORT", "19530")
        self.milvus_collection = _get("MILVUS_COLLECTION", "health_knowledge")
        self.long_memory_collection = _get("LONG_MEMORY_COLLECTION", "long_memory")
        # ChromaDB 作为长期记忆的降级方案
        self.chroma_path = _get("CHROMA_PATH", "./app/chroma_db")

        # ---------------- RAG ----------------
        self.embedding_provider = _get("EMBEDDING_PROVIDER", "dashscope").lower()  # dashscope | huggingface | ollama
        self.embedding_model = _get("EMBEDDING_MODEL", "text-embedding-v3")  # 云端/ollama 用的模型名
        self.embedding_model_path = _get("EMBEDDING_MODEL_PATH", "")  # 本地 HF 模型目录
        self.embedding_device = _get("EMBEDDING_DEVICE", "cpu")  # cpu | cuda
        self.embedding_query_prefix = _get("EMBEDDING_QUERY_PREFIX", "")
        self.chunk_size = _get_int("CHUNK_SIZE", 512)
        self.chunk_overlap = _get_int("CHUNK_OVERLAP", 100)
        # 检索模式：milvus（默认）/ keyword（无向量库时的轻量模式）
        self.rag_mode = _get("RAG_MODE", "milvus").lower()

        # ---------------- 混合检索（dense ∪ BM25 → RRF 融合） ----------------
        # 关闭后行为与升级前完全一致（单路向量召回）
        self.hybrid_retrieval = _get_bool("HYBRID_RETRIEVAL", True)
        self.rrf_k = _get_int("RRF_K", 60)
        self.bm25_cache_ttl = _get_int("BM25_CACHE_TTL", 600)
        self.bm25_docs_dir = _get("BM25_DOCS_DIR", "./knowledge_docs")
        # 向量维度兜底（本地 bge-base-zh-v1.5 = 768；探测失败时使用）
        self.embedding_dim = _get_int("EMBEDDING_DIM", 768)
        # 精排（三级：local 本地模型 → http 云端 → code 纯代码混合打分）
        self.rerank_provider = _get("RERANK_PROVIDER", "code").lower()
        self.rerank_model_path = _get("RERANK_MODEL_PATH", "")
        self.rerank_device = _get("RERANK_DEVICE", "cpu")

        # ---------------- InfluxDB 3（生命体征时序数据） ----------------
        self.influx_url = _get("INFLUX_URL", "http://localhost:8181")
        self.influx_token = _get("INFLUX_TOKEN", "")
        self.influx_database = _get("INFLUX_DATABASE", "agent_db")
        self.vitals_measurement = _get("VITALS_MEASUREMENT", "vital_signs")
        self.vitals_interval_minutes = _get_int("VITALS_INTERVAL_MINUTES", 10)
        self.vitals_backfill_days = _get_int("VITALS_BACKFILL_DAYS", 7)

        # ---------------- 语音 ----------------
        self.vosk_path = _get("VOSK_PATH", "")
        self.tts_voice = _get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
        # 麦克风设备索引（PyAudio 设备号；留空则用系统默认输入设备）
        # 默认设备多为 44.1k/48k 立体声，Windows 会做转换；指定原生支持 16kHz 的设备更稳
        self.mic_device_index = _get("MIC_DEVICE_INDEX", "")

        # ---------------- 邮件 ----------------
        self.email_host = _get("EMAIL_HOST", "smtp.qq.com")
        self.email_to = _get("EMAIL_TO", "")
        self.email_port = _get("EMAIL_PORT", "465")
        self.email_password = _get("EMAIL_PASSWORD", "")

        # ---------------- MCP ----------------
        self.mcp_server_url = _get("MCP_SERVER_URL", "http://localhost:9000")
        self.mcp_api_key = _get("MCP_API_KEY", "health-butler-mcp-key")

        # ---------------- 安全（JWT + RBAC） ----------------
        self.jwt_secret_key = _get("JWT_SECRET_KEY", "health-butler-dev-secret")
        self.jwt_algorithm = _get("JWT_ALGORITHM", "HS256")
        self.jwt_expire_minutes = _get_int("JWT_EXPIRE_MINUTES", 1440)

        # ---------------- 记忆 ----------------
        self.window_memory_rounds = _get_int("WINDOW_MEMORY_ROUNDS", 10)
        self.window_memory_time = _get_int("WINDOW_MEMORY_TIME", 86400)

        # ---------------- 检测与告警 ----------------
        self.detect_interval_minutes = _get_int("DETECT_INTERVAL_MINUTES", 5)
        self.trend_days = _get_int("TREND_DAYS", 7)
        self.trend_deviation = _get_float("TREND_DEVIATION", 0.3)
        self.anomaly_continue_days = _get_int("ANOMALY_CONTINUE_DAYS", 3)
        # 高危短信是否自动发送；false 时走"人工确认"流程
        self.auto_notify_sms = _get_bool("AUTO_NOTIFY_SMS", True)
        # 阈值集中管理（规则引擎第一层）
        self.thresholds = {
            "hr_high": _get_float("HR_HIGH", 120),
            "hr_low": _get_float("HR_LOW", 50),
            "bp_sys_high": _get_float("BP_SYS_HIGH", 180),
            "bp_sys_low": _get_float("BP_SYS_LOW", 90),
            "bp_dia_high": _get_float("BP_DIA_HIGH", 110),
            "spo2_low": _get_float("SPO2_LOW", 90),
            "temp_high": _get_float("TEMP_HIGH", 37.3),
            "temp_low": _get_float("TEMP_LOW", 36.0),
            "glucose_high": _get_float("GLUCOSE_HIGH", 7.0),
            "glucose_low": _get_float("GLUCOSE_LOW", 3.9),
            "sleep_low": _get_float("SLEEP_LOW", 4),
        }

    def redis_client_kwargs(self) -> dict:
        """返回创建 redis.StrictRedis 客户端所需参数"""
        kwargs = {
            "host": self.redis_host,
            "port": self.redis_port,
            "db": self.redis_db,
            "decode_responses": False,
            "socket_timeout": 3,
        }
        if self.redis_password:
            kwargs["password"] = self.redis_password
        return kwargs

    def as_dict(self) -> dict:
        """导出非敏感配置，供 /health 与调试使用"""
        return {
            "model": self.model_line_name,
            "backup_model": self.model_backup_name,
            "local_model": self.model_local_name,
            "mysql": f"{self.db_host}:{self.db_port}/{self.db_name}",
            "postgres": f"{self.pg_host}:{self.pg_port}/{self.pg_db}",
            "redis": f"{self.redis_host}:{self.redis_port}/{self.redis_db}",
            "milvus": f"{self.milvus_host}:{self.milvus_port}/{self.milvus_collection}",
            "influx": f"{self.influx_url}/{self.influx_database}",
            "mcp": self.mcp_server_url,
            "checkpointer": self.checkpointer,
            "rag_mode": self.rag_mode,
        }


# 全局唯一配置实例
settings = Settings()


if __name__ == "__main__":
    import json

    print(json.dumps(settings.as_dict(), ensure_ascii=False, indent=2))
    print("阈值配置：", settings.thresholds)
    print("Redis 参数：", settings.redis_client_kwargs())
    # print(os.getenv("dashscope_api_key"))