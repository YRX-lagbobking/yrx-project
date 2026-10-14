# =============================================================================
# 智能健康管家多智能体系统 —— 应用镜像
# 说明：
#   1. 基础镜像 python:3.11-slim（契约要求 Python 3.11+）；
#   2. 系统依赖：ffmpeg（EdgeTTS/音频处理）、portaudio19-dev + libasound2（PyAudio）、
#      build-essential（部分包需要编译）、libpq-dev（psycopg 连接 PostgreSQL）、
#      curl（健康检查脚本用）；
#   3. 同一个镜像既跑后端（app.main）也跑 MCP Server（mcp_server.mcp_health_server），
#      docker-compose 里用不同 command 区分。
# 构建：docker build -t health-butler:1.0 .
# =============================================================================

FROM python:3.11-slim

# ---- 环境变量：Python 行为与容器时区 ----
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8

WORKDIR /app

# ---- 系统依赖（装完清理 apt 缓存，控制镜像体积） ----
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        portaudio19-dev \
        libasound2 \
        libasound2-dev \
        build-essential \
        libpq-dev \
        libffi-dev \
        libssl-dev \
        curl \
        ca-certificates \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# ---- Python 依赖（先拷 requirements，利用 Docker 层缓存） ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 拷贝项目代码 ----
COPY . .

# ---- 运行期目录（TTS 产物 / 报告导出 / Chroma 持久化） ----
RUN mkdir -p /app/app/html/tts /app/app/html/reports /app/app/chroma_db /app/app/data

# ---- 后端服务端口（MCP Server 由 compose 用 command 覆盖为 9000） ----
EXPOSE 8000

# ---- 健康检查：探测 /health，容器编排据此判断服务是否就绪 ----
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# ---- 启动命令（app/main.py 的 __main__ 会做 Windows 事件循环分支，Linux 下直连 uvicorn） ----
CMD ["python", "-m", "app.main"]
