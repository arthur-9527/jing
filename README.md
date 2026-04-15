# Jing

Jing - AI Agent 后端服务

## 📖 项目简介

Jing 是一个基于 FastAPI 的智能虚拟人后端服务，支持：

- 🎭 **情绪 AI Agent** - 基于大语言模型的智能对话和情绪理解
- 🎬 **动作管理系统** - VMD 动作文件管理和自动调度
- 🗣️ **语音交互** - 支持实时语音识别 (ASR) 和语音合成 (TTS)
- 💾 **长期记忆** - 基于 Vector 的记忆存储和检索
- 🖼️ **前端面板** - 内置 Web 管理界面

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Jing                                          │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  FastAPI    │  │  WebSocket  │  │    Static Files     │ │
│  │  REST API   │  │   Server    │  │  (Frontend Panel)   │ │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘ │
│         │                │                    │             │
│  ┌──────▼────────────────▼────────────────────▼──────────┐  │
│  │                    Agent Service                      │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────┐  │  │
│  │  │   LLM   │ │   ASR   │ │   TTS   │ │  Embedding  │  │  │
│  │  │ Client  │ │ Service │ │ Service │ │   Service   │  │  │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ PostgreSQL  │  │    Redis    │  │   OpenClaw WebSocket │ │
│  │  + pgvector │  │   Cache     │  │   (Motion Executor)  │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 🚀 快速部署

### 环境要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Docker | ≥ 20.10 | 容器运行环境 |
| Docker Compose | ≥ 2.0 | 服务编排工具 |
| 磁盘空间 | ≥ 5GB | 镜像 + 数据 + 模型 |
| 内存 | ≥ 4GB | 推荐 8GB+ |

### 一键部署步骤

```bash
# 1. 进入项目目录
cd jing

# 2. 下载 Embedding 模型（首次部署需要）
./scripts/download-embedding-model.sh

# 3. 复制配置文件模板
cp .env.example .env

# 4. 编辑配置文件，填入必要的 API Keys
vim .env  # 或使用其他编辑器

# 5. 启动服务
./start.sh --build
```

### 服务管理

```bash
# 启动服务（后台运行）
./start.sh

# 启动服务（重新构建镜像）
./start.sh --build

# 启动服务（开发模式，前台运行）
./start.sh --dev

# 停止服务
./stop.sh

# 停止服务并清理数据卷
./stop.sh --clean

# 查看日志
docker-compose logs -f jing
```

### 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 就绪检查
curl http://localhost:8000/ready

# API 文档
open http://localhost:8000/docs
```

## ⚙️ 配置说明

配置文件：`.env`（从 `.env.example` 复制）

### 必需配置项

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `POSTGRES_PASSWORD` | PostgreSQL 密码 | `your_secure_password` |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope API Key | `sk-xxx` |
| `LLM_API_BASE_URL` | LLM 服务地址 | `http://your-llm-server:4000/v1` |
| `LLM_API_KEY` | LLM API Key | `sk-xxx` |
| `OPENCLAW_WS_URL` | OpenClaw WebSocket 地址 | `ws://host.docker.internal:18789/gateway` |
| `OPENCLAW_WS_TOKEN` | OpenClaw WebSocket Token | `your_token` |

### 可选配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `LLM_PROVIDER` | `cerebras` | LLM 提供商 (`litellm` / `cerebras`) |
| `LLM_MODEL` | `qwen3-chat` | LLM 模型名称 |
| `ASR_PROVIDER` | `qwen` | ASR 提供商 (`qwen` / `deepgram`) |
| `TTS_PROVIDER` | `cosyvoice_ws` | TTS 提供商 (`cosyvoice_ws` / `cartesia`) |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `PORT` | `8000` | 服务端口 |

### Embedding 模型配置

系统使用本地 Embedding 模型，需要预先下载：

```bash
# 自动下载 BAAI/bge-small-zh-v1.5 模型（约 100MB）
./scripts/download-embedding-model.sh

# 模型将保存到 ./models/embedding 目录
# Docker 会自动挂载到容器内
```

### 角色配置

角色配置文件位于 `config/characters/` 目录：

```
config/characters/{character_id}/
├── character.json     # 角色配置
├── personality.md     # 性格描述
├── model/
│   └── {character_id}.pmx  # MMD 模型（强制同名）
│   └── Texture/            # 模型纹理（可选）
└── {character_id}.mp3      # 音频克隆文件（强制同名）
```

## 📡 API 端点

### 健康检查

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查（检查所有依赖服务状态） |
| `GET /ready` | 就绪检查（检查服务是否可接收请求） |
| `GET /live` | 存活检查（简单进程存活检查） |

### 主要 API

| 端点 | 说明 |
|------|------|
| `GET /docs` | Swagger API 文档 |
| `GET /redoc` | ReDoc API 文档 |
| `WS /ws/agent` | Agent WebSocket 接口 |
| `GET /api/motions` | 动作列表 |
| `GET /api/tags` | 标签列表 |
| `POST /vmd/upload` | VMD 文件上传 |

### 前端页面

| 路径 | 说明 |
|------|------|
| `/` | 主前端页面 |
| `/vmd` | VMD 上传管理页面 |

## 🐳 Docker 配置

### 构建镜像

```bash
# 在项目父目录下构建（需要访问 frontend 和 vmd2sql）
cd ..
docker build -t jing:latest -f jing/Dockerfile .

# 或使用 docker-compose
cd jing
docker-compose build --no-cache
```

### 手动运行

```bash
# 启动依赖服务
docker-compose up -d postgres redis

# 等待数据库就绪
docker-compose logs -f postgres

# 启动主服务
docker-compose up -d jing
```

### 数据卷说明

| 卷 | 容器路径 | 说明 |
|----|----------|------|
| `postgres_data` | `/var/lib/postgresql/data` | PostgreSQL 数据 |
| `redis_data` | `/data` | Redis 数据 |
| `./models/embedding` | `/app/models/embedding` | Embedding 模型 |
| `./config` | `/app/config` | 角色配置 |
| `./logs` | `/app/logs` | 日志文件 |
| `./temp` | `/app/temp` | 临时文件 |

## 📁 目录结构

```
jing/
├── app/                      # 应用主目录
│   ├── agent/               # Agent 核心
│   │   ├── character/      # 角色加载器
│   │   ├── db/             # 数据库模型
│   │   ├── emotion/        # 情绪系统
│   │   ├── llm/            # LLM 客户端
│   │   ├── memory/         # 记忆系统
│   │   ├── motion/         # 动作匹配
│   │   └── prompt/         # 提示词模板
│   ├── executors/          # 执行器
│   ├── models/             # 数据模型
│   ├── routers/            # API 路由
│   ├── scheduler/           # 定时任务
│   ├── schemas/            # Pydantic 模型
│   └── services/            # 业务服务
│       ├── chat_history/   # 对话历史
│       ├── emotion/        # 情绪引擎
│       ├── frame_queue/    # 帧队列
│       ├── llm/            # LLM 服务
│       ├── openclaw/       # OpenClaw 客户端
│       ├── playback/       # 播放管理
│       ├── stt/            # 语音识别
│       └── tts/            # 语音合成
├── config/                  # 配置目录
│   └── characters/         # 角色配置
├── scripts/                 # 工具脚本
├── sql/                     # SQL 脚本
├── tests/                   # 测试用例
├── models/                  # 模型文件
│   └── embedding/          # Embedding 模型
├── logs/                    # 日志目录
├── temp/                    # 临时文件
├── dist/                    # 主前端构建输出
├── dist-vmd2sql/           # VMD 前端构建输出
├── Dockerfile              # Docker 镜像构建
├── docker-compose.yml      # 服务编排配置
├── start.sh                # 启动脚本
├── stop.sh                 # 停止脚本
├── main.py                 # 应用入口
├── requirements.txt        # Python 依赖
└── .env.example            # 配置模板
```

## ❓ 常见问题

### 1. 镜像构建失败：找不到 frontend 目录

**原因**：Dockerfile 需要在父目录构建，依赖 `frontend` 和 `vmd2sql/frontend` 项目。

**解决**：
```bash
# 确保目录结构正确
ls -la ../frontend ../vmd2sql/frontend

# 或修改 docker-compose.yml 中的 context
```

### 2. Embedding 模型加载失败

**原因**：模型文件未下载或路径不正确。

**解决**：
```bash
# 下载模型
./scripts/download-embedding-model.sh

# 检查模型文件
ls -la models/embedding/
# 应包含：config.json, model.safetensors 等

# 检查 .env 配置
grep EMBEDDING_MODEL_PATH .env
```

### 3. 数据库连接失败

**原因**：PostgreSQL 未就绪或密码不匹配。

**解决**：
```bash
# 检查 PostgreSQL 状态
docker-compose logs postgres

# 确认密码一致
grep POSTGRES_PASSWORD .env

# 重启数据库
docker-compose restart postgres
```

### 4. OpenClaw WebSocket 连接失败

**原因**：OpenClaw 服务未启动或地址配置错误。

**解决**：
```bash
# 检查 OpenClaw 服务状态
curl http://localhost:18789/health

# 检查配置
grep OPENCLAW_WS .env

# Docker 内部访问宿主机
# 使用 host.docker.internal 或 host-gateway
OPENCLAW_WS_URL=ws://host.docker.internal:18789/gateway
```

### 5. ASR/TTS 调用失败

**原因**：DashScope API Key 未配置或无效。

**解决**：
```bash
# 检查 API Key
grep DASHSCOPE_API_KEY .env

# 测试 API Key
curl -X POST https://dashscope.aliyuncs.com/api/v1/services/audio/asr \
  -H "Authorization: Bearer $DASHSCOPE_API_KEY" \
  ...
```

### 6. 服务启动后健康检查失败

**原因**：依赖服务未就绪。

**解决**：
```bash
# 查看详细日志
docker-compose logs -f jing

# 检查所有服务状态
docker-compose ps

# 手动健康检查
curl -v http://localhost:8000/health
```

## 📝 开发指南

### 本地开发环境

```bash
# 安装依赖
pip install -r requirements.txt

# 创建 PostgreSQL 数据库
createdb jing

# 启动 Redis
redis-server

# 运行服务
python main.py
```

### 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行特定测试
pytest tests/test_ws_connection.py -v
```

## 📄 License

本项目采用 [MIT License](LICENSE) 开源许可证。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！