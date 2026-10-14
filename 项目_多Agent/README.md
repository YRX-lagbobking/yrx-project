# 智能健康管家多智能体系统

> 基于 **LangGraph** 的独居老人居家健康管理多智能体系统
> 语音问健康 · 趋势盯异常 · 家属收通知 · 一链闭环

| 项目 | 说明 |
| :--- | :--- |
| 技术栈 | FastAPI + LangGraph + LangChain + Vue2(CDN) |
| 存储 | MySQL 8 / PostgreSQL 16 / Redis 7 / InfluxDB 3 Core / Milvus 2.4 |
| 角色 | 老人（elder）/ 家属（family）/ 管理员（admin） |
| 部署 | Docker Compose 一键拉起（Windows / Linux 均可） |
| 需求基线 | [需求文档.md](需求文档.md) · [开发规范与接口契约.md](docs/开发规范与接口契约.md) |

---

## 一、项目简介

我国老龄化程度持续加深，独居老人居家健康管理有四个典型痛点：

1. **不会用** —— 智能设备与 App 操作门槛高，老人不习惯打字、看不清小字；
2. **没人问** —— 子女不在身边，身体异常无人察觉，求助链路长；
3. **看不出来** —— 心率、血压单次看都正常，但"持续缓慢恶化"的趋势没人帮他盯；
4. **通知不到** —— 真正出风险时，家属往往最后一个知道。

本系统围绕这四点，做了一套**面向独居老人个人场景**的多智能体方案：
老人说一句话就能问健康，后端常驻服务替他盯趋势，命中风险自动双通道通知家属，
并生成可下载的健康报告；全链路只做**健康提示**，不做医疗诊断。

---

## 二、功能亮点

| 亮点 | 说明 | 对应需求 |
| :--- | :--- | :--- |
| 🎙️ **语音一问一答** | WebSocket + Vosk 离线识别，支持**连续多句**与随时停止；回答用 EdgeTTS 自动播报 | FR-01、FR-03 |
| 🧭 **LangGraph Supervisor 编排** | Planner 拆任务 → Manager 用 `Command(goto=...)` 动态路由 → Worker 回主管 → Summarize 汇总 | FR-10 |
| 📈 **三层风险检测** | ①规则阈值 ②统计趋势（7 天移动平均 + 30% 偏离 + 连续 3 天升级）③个性化离群（MAD / 孤立森林） | FR-06 |
| 🔒 **检测无副作用** | `detect_anomaly()` 是纯计算层；手动检测不写库、不通知，**不污染自动预警统计口径** | AC-10 |
| 🚨 **双通道告警闭环** | 通道 1 Celery → MCP 短信/邮件 → 家属；通道 2 WebSocket → 老人端弹窗 + 播报 + "我已知道"回执 | FR-07 |
| 📚 **RAG 溯源** | 知识片段带 `source / version / page`，每条建议都能回溯到原文；Milvus 挂掉自动降级关键词检索 | FR-09 |
| 🧠 **四层记忆** | L1 Redis 窗口 / L2 PG 摘要 / L3 Milvus 长期 / L4 Redis 画像，组装成 system 提示词 | FR-13 |
| 🧩 **Skill 渐进式加载** | 只先读元数据，命中后才拉取完整指令；新增技能目录即可用，**不改代码** | FR-12 |
| 💊 **用药提醒邮件** | "帮我给张三发一封用药提醒邮件" → 取档案 → 取计划 → 生成温和文案 → 真发邮件 | FR-04 |
| 🛡️ **六层降级** | 模型 / 工具 / 节点 / RAG / 服务 / 告警，逐层兜底，单点故障不崩站 | FR-15 |
| 🔐 **JWT + RBAC + DAO 隔离** | 老人只见自己、家属只见绑定老人；MCP 工具有 KeyVerifier 白名单 | FR-11 |

---

## 三、五层架构

```
┌───────────────────────────────────────────────────────────────────────────┐
│ ① 前端层  Vue2(CDN) + marked —— 登录 / 聊天(SSE+WS语音) / 报告 / 家属端    │
└───────────┬──────────────────────┬──────────────────────┬────────────────┘
            │ HTTP                 │ SSE                  │ WebSocket
            ▼                      ▼                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ ② 接入层  FastAPI + StaticFiles                                           │
│   JWT 鉴权 │ RBAC 授权 │ CORS │ 路由分发 │ WS 连接管理                     │
│   /user/*  /create_session  /chat  /voice/*  /report/*  /alert/*  /health  │
└───────────────────────────┬───────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ ③ 编排层  LangGraph 多智能体（Supervisor + Command 动态路由 + 任务队列）    │
│   planner → manager → {intent, data_check, rag, risk, report, remind,      │
│                        notify, chat} → manager → summarize → END          │
│   Checkpointer（PostgresSaver / InMemorySaver，thread_id = session_id）    │
└───────┬───────────────────┬───────────────────┬───────────────────────────┘
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ MCP Client    │   │ 四层记忆 L1~L4│   │ 异步任务      │
│ 工具调用转发  │   │ 读写/组装提示 │   │ Celery 通知   │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        ▼                   ▼                   ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ ④ 能力层  MCP Server（端口 9000，KeyVerifier 鉴权，工具白名单）            │
│   档案查询 │ 用药查询 │ 时序查询 │ RAG 检索 │ 短信/邮件通知 │ 报告生成      │
└───────────────────────────┬───────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ ⑤ 存储层                                                                  │
│   MySQL: 用户/档案/用药/告警/知识文档   PostgreSQL: 摘要记忆 + Checkpointer │
│   Redis: 窗口记忆/画像/会话/锁/队列     Milvus: 知识向量 + 长期记忆          │
│   InfluxDB 3: vital_signs 时序（心率/血压/血氧/体温/血糖/睡眠）             │
└───────────────────────────────────────────────────────────────────────────┘
```

> 更详细的图（状态机、双通道告警时序、数据连续性策略、降级总览、RAG 流程、语音链路）
> 见 [docs/系统架构图.md](docs/系统架构图.md)。

---

## 四、目录结构

```
agent_project/
├── 需求文档.md                  # 需求规格说明书（唯一业务事实来源）
├── README.md                    # 本文件
├── requirements.txt             # 依赖清单（分组注释 + >= 下限）
├── .env.example                 # 环境变量模板（复制为 .env 后填敏感值）
├── .gitignore                   # 忽略密钥 / 生成物 / 缓存
├── Dockerfile                   # python:3.11-slim + ffmpeg/portaudio/libpq
├── docker-compose.yml           # MySQL/PG/Redis/InfluxDB/Milvus/MCP/App 一键起
│
├── docs/
│   ├── 开发规范与接口契约.md      # 【锁定】接口与签名的唯一事实来源
│   ├── 系统架构图.md             # 五层架构 / 状态机 / 时序 / 降级
│   ├── 接口文档.md               # 9.1/9.2/9.3 三张表的展开版
│   └── 数据库设计.md             # 8 张 MySQL 表 + Influx/Redis/Milvus 模型
│
├── scripts/
│   └── check_project.py         # 项目自检（语法 + 导入 + 结构）
│
├── sql/
│   └── init.sql                 # 建库建表 + 初始化数据（含密码哈希对照表）
│
├── mcp_server/                  # MCP 工具服务（端口 9000）
│   ├── mcp_health_server.py      # FastMCP / 降级 FastAPI + KeyVerifier
│   └── tools/                    # 纯函数实现，可脱离 MCP 单测
│       ├── profile_tool.py       # get_user_profile
│       ├── medication_tool.py    # get_medication_schedule
│       ├── vitals_tool.py        # get_vitals
│       ├── knowledge_tool.py     # search_knowledge
│       ├── report_tool.py        # generate_report
│       └── notify_tool.py        # send_sms / send_email
│
└── app/
    ├── main.py                   # FastAPI 入口（lifespan 装配一切）
    ├── core/                     # 配置 / 日志 / 安全 / 降级 / 检测 / Celery
    │   ├── config.py             # 【锁定】settings 单例
    │   ├── security.py           # JWT + RBAC + hash_password
    │   ├── health_check.py       # check_all() / detect_mode()
    │   ├── detect/               # 规则 / 趋势 / 离群 三层检测（纯计算）
    │   └── task/                 # notify_task / detect_task（Celery）
    ├── dao/                      # 数据访问层（同步 pymysql，全员参数化）
    ├── skills/                   # Skill 技能包（渐进式加载）
    │   ├── skill_loader.py       # list_skills / get_skill_meta / load_skill
    │   └── registry/             # medication_reminder / weekly_report /
    │                             # daily_report / emotion_comfort 的 SKILL.md
    ├── ai/                       # 模型 / 提示词 / 工具 / 智能体
    │   ├── model/my_model.py     # 主/备/本地模型 + Embedding + Rerank
    │   ├── tool/                 # mysql / influx / rag / mcp / send_email / skill
    │   └── agent/
    │       ├── vosk_agent.py     # 语音识别（连续多句）
    │       ├── tts_agent.py      # EdgeTTS 语音合成
    │       ├── memory/           # 四层记忆 + 提示词组装
    │       └── multi_agent/      # state / schema / node / graph
    ├── web/                      # FastAPI 路由（HTTP / SSE / WebSocket）
    ├── html/                     # 前端页面（单文件自洽，CDN 依赖）
    │   ├── login.html  chat.html  report.html  family.html
    └── test/                     # 12 个教学式自测脚本
```

---

## 五、快速开始

### 步骤 1：准备环境

```bash
# 需要 Python 3.11+ 与 Docker Desktop（或 Docker Engine + Compose v2）
python --version
docker --version
docker compose version
```

### 步骤 2：安装 Python 依赖

```bash
# 建议用虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

> 若 `pyaudio` 安装失败，见 [常见问题](#七常见问题faq) 第 4 条。

### 步骤 3：初始化 MySQL

```bash
# 方式 A：本机已有 MySQL 8
mysql -uroot -p < sql/init.sql

# 方式 B：用容器里的 MySQL（推荐，和第 4 步一起做）
docker compose up -d mysql
docker exec -i health-mysql mysql -uroot -p1234 < sql/init.sql
```

初始化完成后应有 **6 个用户**（3 老人 + 2 家属 + 1 管理员）、7 条用药计划、
10 条知识文档、5 条告警记录、12 条用药记录。

### 步骤 4：启动中间件（Docker Compose）

```bash
docker compose up -d
docker compose ps          # 等所有服务变成 healthy
```

拉起 9 个服务：`mysql` `postgres` `redis` `influxdb` `etcd` `minio` `milvus`
`mcp-server` `app`。

> **首次启动 InfluxDB 若失败，先修数据卷权限**（见下方"数据卷权限修复"）。
> 只是本地开发、不想跑 Milvus 的话，可以只起核心几个：
> `docker compose up -d mysql postgres redis influxdb`

> **不想全用容器？** 本项目支持"混合模式"：MySQL / PostgreSQL / Redis 装在**本机**，
> 只把 Milvus 与 InfluxDB 放 Docker，后端与 MCP Server 直接在 Windows 上跑。
> 逐步说明见 **[docs/本机运行部署指南.md](docs/本机运行部署指南.md)**，一条命令：
> `docker compose -f docker-compose.middleware.yml up -d`
> ⚠️ 混合模式下**不要**执行 `docker compose up -d`（会抢占 3306/5432/6379/8000 端口）。

#### InfluxDB 数据卷权限修复（R-03）

InfluxDB 3 Core 容器内以 UID **1500** 运行，挂载空数据卷时可能因归属为 root 而启动失败。
修复命令：

```bash
docker run --rm --user root -v influxdb3_data:/var/lib/influxdb3/data \
  influxdb:3-core chown -R 1500:1500 /var/lib/influxdb3/data
```

### 步骤 5：配置 .env

```bash
# Windows
copy .env.example .env
# Linux / macOS
cp .env.example .env
```

然后填入这些**必填的敏感项**（其余保持默认即可）：

| 变量 | 说明 |
| :--- | :--- |
| `DASHSCOPE_API_KEY` | 主模型（通义千问）Key，不填会走三级降级到静态文案 |
| `BACKUP_API_KEY` | 备用云端模型 Key（可留空） |
| `INFLUX_TOKEN` | InfluxDB 3 的 Token，**必须与建库时用的一致** |
| `EMAIL_PASSWORD` | 邮箱 SMTP 授权码（用药提醒邮件/家属邮件通知需要） |
| `MCP_API_KEY` | MCP 工具鉴权 Key（自定义随机串即可，前后端一致） |
| `VOSK_PATH` | 中文语音模型目录绝对路径（不填则语音识别不可用） |
| `JWT_SECRET_KEY` | 生产环境务必换成随机长字符串 |

### 步骤 6：创建 InfluxDB 数据库

```bash
# Token 用 .env 里的 INFLUX_TOKEN
docker exec -it health-influxdb influxdb3 create database \
  --retention-period 30d --token <你的 INFLUX_TOKEN> agent_db

# 验证
docker exec -it health-influxdb influxdb3 show databases --token <你的 INFLUX_TOKEN>
```

> 保留期只能在建库时设定（InfluxDB 3 Core 不支持按点删除，风险 R-02）。
> 本地直接装的 InfluxDB 3 用同样的 `influxdb3 create database ...` 命令。

### 步骤 7：启动 MCP Server（端口 9000）

```bash
# 容器方式（已随 docker compose up -d 启动）
docker compose logs -f mcp-server

# 本地方式
python -m mcp_server.mcp_health_server

# 自测（需带 Key）
curl -H "Authorization: Bearer <MCP_API_KEY>" http://localhost:9000/healthz
```

### 步骤 8：启动后端

```bash
# 方式 A（推荐，app/main.py 的 __main__ 已处理 Windows 事件循环）
python -m app.main

# 方式 B：uvicorn 直启
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开浏览器访问 **http://localhost:8000** → 自动跳到登录页
（`/` → 重定向 `/static/login.html`）。

**演示账号**

| 角色 | 用户名 | 密码 | 登录后跳转 |
| :--- | :--- | :--- | :--- |
| 老人 | `zhangsan`（u001） | `123456` | `/static/chat.html` |
| 老人 | `lisi`（u002） | `123456` | `/static/chat.html` |
| 老人 | `wangwu`（u003） | `123456` | `/static/chat.html` |
| 家属 | `zhangxiaoming`（f001） | `123456` | `/static/family.html` |
| 家属 | `lixiaohong`（f002） | `123456` | `/static/family.html` |
| 管理员 | `admin` | `admin123456` | `/static/report.html` |

### 步骤 9：启动 Celery Worker / Beat（异步通知与定时检测）

需要**另开两个终端**（都在项目根目录，且已激活虚拟环境）：

```bash
# Windows（必须加 -P solo，否则事件循环/子进程会出问题）
celery -A app.core.celery_app worker -l info -P solo

# Linux / macOS
celery -A app.core.celery_app worker -l info
```

```bash
# 定时任务（每 DETECT_INTERVAL_MINUTES 分钟跑一次全量自动检测）
celery -A app.core.celery_app beat -l info
```

> Celery 为必装依赖（Redis 作 broker / backend）：`notify_family.delay()` 投递后由 Worker 异步发送，失败按 `max_retries=3, countdown=60s` 重试（AC-12）。

---

## 六、接口速查表

### 6.1 HTTP / SSE（详见 [docs/接口文档.md](docs/接口文档.md)）

| 方法 | 路径 | 说明 | 鉴权 |
| :--- | :--- | :--- | :--- |
| POST | `/user/register` | 注册（角色选择） | 否 |
| POST | `/user/login` | 登录，签发 JWT | 否 |
| GET | `/user/me` | 当前用户信息与画像、绑定老人 | JWT |
| POST | `/user/bind_family` | 家属绑定老人 | JWT（family） |
| POST | `/create_session` | 创建 / 校验会话 | JWT |
| GET | `/chat` | **SSE 流式问询** | JWT（支持 `?token=`） |
| POST | `/chat/skill` | 指定 Skill 触发 | JWT |
| POST | `/voice/chat` | 音频文件一次性问询 | JWT |
| POST | `/voice/tts` | 文本转语音，返回 mp3 URL | JWT |
| GET | `/report/daily` | 日报生成 | JWT |
| GET | `/report/weekly` | 周报生成 | JWT |
| GET | `/report/download` | 报告导出 Word | JWT |
| GET | `/alert/list` | 告警列表（按角色过滤） | JWT |
| POST | `/alert/{alert_id}/confirm` | 告警确认（闭环） | JWT |
| POST | `/alert/detect` | 手动触发检测（**无副作用**） | JWT |
| GET | `/health` | 服务健康与降级模式 | 否 |

### 6.2 WebSocket

| 路径 | 方向 | 说明 |
| :--- | :--- | :--- |
| `/voice/ws` | 双向 | 语音识别：服务端推识别文本/`开始说话`；客户端可发 `停止` 或 `{"action":"stop"}` |
| `/ws/notify?user_id=` | 服务端→客户端 | 告警推送 `{"type":"health_alert","alert_id","level","message","action":"voice_play"}` |

### 6.3 MCP 工具（端口 9000，`Authorization: Bearer <MCP_API_KEY>`）

| 工具名 | 入参 | 出参 |
| :--- | :--- | :--- |
| `get_user_profile` | `name` 或 `user_id` | 结构化健康档案 |
| `get_medication_schedule` | `user_id` / `name` | 用药计划列表 |
| `get_vitals` | `user_id`、`field`、`days` | 时序点 / 统计 |
| `search_knowledge` | `query`、`top_k`、`disease_type` | 带溯源的知识片段 |
| `send_sms` | `to`、`content` | 发送结果 |
| `send_email` | `to`、`subject`、`content` | 发送结果 |
| `generate_report` | `user_id`、`type` | 报告正文（Markdown） |
| `send_email_tool` | `to`、`subject`、`content` | 本地工具，仅报告/提醒节点用 |

### 6.4 SSE 事件格式

```
data: {"data": "片段文本", "done": false}

data: {"data": "", "done": true}
```

---

## 七、自测脚本

12 个教学式脚本，都能独立运行（不依赖 pytest）：

```bash
python -m app.test.test_skill          # Skill 加载（AC-22）
python -m app.test.test_security       # JWT + RBAC 正反例（AC-19/AC-20）
python -m app.test.test_vosk           # 麦克风连续多句识别（AC-01/AC-02）
python -m app.test.test_agent          # create_agent + 工具 + 异步流式
python -m app.test.test_influx         # 时序写入 + 近 7 天查询（AC-25）
python -m app.test.test_rag            # 切分 / 建库 / 检索溯源（AC-14/AC-15）
python -m app.test.test_detect         # 三层检测 + 手动检测无副作用（AC-08~AC-10）
python -m app.test.test_graph          # 复合意图编排 + 流式（AC-16~AC-18）
python -m app.test.test_memory         # 四层记忆 save/query（AC-23/AC-24）
python -m app.test.test_notify         # 异步通知 + 本地兜底队列（AC-11/AC-12）
python -m app.test.test_report         # 报告生成 + Word 导出（AC-13）
python -m app.test.test_health_check   # 健康检查与降级模式（AC-26）
```

缺少依赖或服务时脚本会**打印降级说明并正常退出**，不会抛异常（NFR-02）。

项目结构自检：`python scripts/check_project.py`

---

## 八、常见问题（FAQ）

### 1. InfluxDB 容器起不来 / 报权限错误

容器内以 UID 1500 运行，空数据卷归属 root 时会失败（风险 R-03）。执行：

```bash
docker run --rm --user root -v influxdb3_data:/var/lib/influxdb3/data \
  influxdb:3-core chown -R 1500:1500 /var/lib/influxdb3/data
docker compose up -d influxdb
```

另外确认 `.env` 里的 `INFLUX_TOKEN` 与建库命令用的 Token 完全一致，
否则会报 `unauthorized`。

### 2. Milvus 内存不够 / 一直重启

Milvus standalone 至少需要 **4G 内存**（含 etcd + MinIO，风险 R-04）。三种应对：

```bash
# 方案 A：给 Docker Desktop 提高内存上限（推荐 ≥ 8G）
# 方案 B：干脆不跑 Milvus，用关键词检索模式
#   在 .env 里设置：
#   RAG_MODE=keyword
docker compose up -d mysql postgres redis influxdb   # 只起核心存储

# 方案 C：用 ChromaDB 替代（长期记忆会自动降级）
#   .env 里 CHROMA_PATH=./app/chroma_db 即可，无需额外服务
```

`RAG_MODE=keyword` 时检索走 MySQL `knowledge_doc`，接口与溯源字段完全一致。

### 3. Vosk 模型路径

```env
# 解压后的模型文件夹**绝对路径**，注意不要多一层或少一层目录
VOSK_PATH=E:\vosk-model-cn-0.22\vosk-model-cn-0.22
```

模型下载：https://alphacephei.com/vosk/models （选 `vosk-model-cn-0.22`，约 1.3G）。

验证方式：`python -m app.test.test_vosk` —— 能连续打印多句识别结果即正常。
模型目录不存在时，脚本与后端都会打印提示并降级（语音功能不可用，其它功能不受影响）。

### 4. PyAudio 安装失败（Windows）

```bash
pip install pipwin
pipwin install pyaudio
# 或下载对应 Python 版本的 .whl 手动安装：
#   https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio
```

Linux 上需要系统依赖：`sudo apt-get install portaudio19-dev libasound2-dev`
（Dockerfile 已包含）。

### 5. Windows 事件循环与 Vosk / PyAudio 冲突

Vosk 与 PyAudio 在 Windows 下需要 `SelectorEventLoop`：

```python
# app/main.py 的 __main__ 里已处理
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

所以请用 `python -m app.main` 启动（而不是 `uvicorn app.main:app`），
否则可能出现 `NotImplementedError` 或麦克风打不开。

### 6. Windows 下 Celery 报错 / 卡住

```bash
# 必须加 -P solo（Windows 默认的 prefork 池不可用）
celery -A app.core.celery_app worker -l info -P solo
```

### 7. 报告下载 401 / 403

`/report/download` 需要鉴权，普通 `<a href>` 不会带 `Authorization` 头。
`report.html` 已用 `fetch + blob` 方式下载；如果你自己写页面，记得：

```javascript
fetch(url, { headers: { 'Authorization': 'Bearer ' + localStorage.getItem('token') } })
  .then(res => res.blob())
  .then(blob => { /* 触发下载 */ });
```

### 8. `EventSource` 无法带 Token

`EventSource` 不支持自定义请求头，所以 `/chat` 额外支持 `?token=<JWT>` 查询参数。
前端 `chat.html` 已按此方式实现。（生产建议改用 WSS + Cookie 或 fetch-stream。）

### 9. `/health` 显示 partial，但功能还能用

这是**预期行为**：系统按 `mode` 逐层降级而不是整体不可用（NFR-02）。
查看 `detail` 里哪一项是 `false`，对照下表处理：

| 不可用项 | 影响 | 处理 |
| :--- | :--- | :--- |
| `milvus` | RAG 降级关键词检索 | 可接受；或设 `RAG_MODE=keyword` |
| `influxdb` | 无时序数据，风险检测走简化版 | 检查 Token / 建库命令 |
| `mcp_server` | 工具走本地实现 + 缓存 | `docker compose logs mcp-server` |
| `redis` | 窗口记忆/会话锁失效，告警落本地队列 | 检查 Redis 容器 |
| `postgres` | Checkpointer 降级内存版（重启丢记忆） | 检查 PG 容器与连接串 |
| `mysql` | 业务数据不可读写（只读模式） | 检查容器与 `sql/init.sql` |

### 10. 端口冲突

`docker-compose.yml` 占用的宿主端口：`3306 / 5432 / 6379 / 8181 / 19530 / 9091 / 9000 / 9001 / 8000`。
Milvus 的 MinIO **内部 9000 端口不与 MCP Server 冲突**（只在容器网络内暴露，
控制台映射到 9001）。若 8000 被占用，改 `app` 服务的 ports 映射或直接本地跑。

---

## 九、免责声明

本系统输出的一切内容均为**健康提示与生活方式建议，不构成医疗诊断或处方**。
任何指标异常请以医疗机构检测结果为准；出现胸痛、意识模糊、呼吸困难、
一侧肢体无力等紧急症状，请立即拨打 **120**。

系统不做以下事情：
- 不给出确诊结论、病因推断与具体用药方案调整；
- 不替代医生面诊与器械检查；
- 不承诺告警必达（已做五级兜底，但仍依赖网络与运营商通道）。

隐私说明：健康数据访问受 JWT + RBAC + DAO 层 `user_id` 强制过滤保护；
日志对手机号与邮箱做脱敏；不留存原始音频；`.env` 中的密钥不会进入 Git。

---

## 十、相关文档

| 文档 | 内容 |
| :--- | :--- |
| [需求文档.md](需求文档.md) | 需求规格说明书（角色、场景、FR/NFR、验收标准、里程碑、风险） |
| [docs/开发规范与接口契约.md](docs/开发规范与接口契约.md) | **唯一接口事实来源**：目录结构、锁定签名、路由与工具清单 |
| [docs/系统架构图.md](docs/系统架构图.md) | 五层架构、LangGraph 状态机、双通道告警时序、数据连续性、六层降级 |
| [docs/接口文档.md](docs/接口文档.md) | 每个接口的方法/路径/鉴权/参数/响应示例/错误码 |
| [docs/数据库设计.md](docs/数据库设计.md) | 8 张 MySQL 表字段表 + Influx/Redis/Milvus 模型 + 表关系 |
| [sql/init.sql](sql/init.sql) | 建库建表 + 初始化数据 + 密码哈希对照表 |
