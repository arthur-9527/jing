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

---

### 方式一：拉取镜像部署（推荐 ✨）

**适用于生产环境，无需本地构建，开箱即用。**

#### 步骤 1：创建部署目录

```bash
mkdir ~/jing && cd ~/jing
```

#### 步骤 2：拉取预构建镜像

```bash
docker pull hostname9527/jing:latest
docker pull hostname9527/jing-postgres:latest
```

#### 步骤 3：下载部署配置文件

```bash
# 下载 docker-compose.deploy.yml
wget https://raw.githubusercontent.com/arthur-9527/jing/main/docker-compose.deploy.yml

# 或手动创建（如果网络受限）
curl -LO https://raw.githubusercontent.com/arthur-9527/jing/main/docker-compose.deploy.yml
```

#### 步骤 4：创建配置文件

```bash
cat > .env << 'EOF'
# ========================================
# 数据库配置（用户自行设置账号密码）
# ========================================
POSTGRES_USER=myuser
POSTGRES_PASSWORD=my_secure_password
POSTGRES_DB=agent_backend

# ========================================
# API 配置（必须填写）
# ========================================
# 阿里云 DashScope（用于 ASR/TTS）
DASHSCOPE_API_KEY=your-dashscope-api-key

# LLM 服务配置
LLM_PROVIDER=cerebras
LLM_API_BASE_URL=http://your-llm-server:4000/v1
LLM_API_KEY=your-llm-api-key
LLM_MODEL=qwen3-chat

# OpenClaw WebSocket（动作执行器）
OPENCLAW_WS_URL=ws://host.docker.internal:18789/gateway
OPENCLAW_WS_TOKEN=your-token

# ========================================
# Embedding 模型路径（本地路径）
# ========================================
EMBEDDING_MODEL_PATH=./models/embedding
EOF
```

#### 步骤 5：下载 Embedding 模型

```bash
# 创建模型目录
mkdir -p models/embedding

# 使用 HuggingFace CLI 下载（需要 pip install huggingface_hub）
huggingface-cli download BAAI/bge-small-zh-v1.5 --local-dir models/embedding

# 或使用项目脚本
wget https://raw.githubusercontent.com/arthur-9527/jing/main/scripts/download-embedding-model.sh
chmod +x download-embedding-model.sh
./download-embedding-model.sh
```

#### 步骤 6：启动服务

```bash
docker-compose -f docker-compose.deploy.yml up -d
```

#### 步骤 7：验证部署

```bash
# 检查服务状态
docker-compose -f docker-compose.deploy.yml ps

# 健康检查
curl http://localhost:8000/health

# 查看日志
docker-compose -f docker-compose.deploy.yml logs -f jing
```

---

### 方式二：本地构建部署

**适用于开发环境或自定义修改。**

```bash
# 1. 克隆项目
git clone https://github.com/arthur-9527/jing.git
cd jing

# 2. 下载 Embedding 模型
./scripts/download-embedding-model.sh

# 3. 复制配置文件模板
cp .env.example .env

# 4. 编辑配置文件
vim .env

# 5. 启动服务（自动构建镜像）
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

### 多平台镜像支持

Jing Docker 镜像支持以下平台：

| 平台 | 架构 | 适用设备 |
|------|------|----------|
| `linux/amd64` | x86_64 | 通用服务器、云主机 |
| `linux/arm64` | ARM64 | 树莓派 5、Apple Silicon Mac |

```bash
# 查看镜像支持的架构
docker inspect hostname9527/jing:latest --format '{{.Architecture}}'

# 拉取时会自动选择匹配当前设备的架构
docker pull hostname9527/jing:latest
```

---

### 方式一：自动构建（GitHub Actions）✨

**推荐用于正式发布**

#### 配置 GitHub Secrets

在 GitHub 仓库 Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 |
|--------|------|
| `DOCKER_USERNAME` | Docker Hub 用户名 |
| `DOCKER_PASSWORD` | Docker Hub Access Token（推荐） |

#### 触发构建

```bash
# 推送标签触发自动构建
git tag v1.0.0
git push origin v1.0.0

# 构建完成后自动推送到 Docker Hub
# 镜像：hostname9527/jing:v1.0.0, hostname9527/jing:latest
# 镜像：hostname9527/jing-postgres:v1.0.0, hostname9527/jing-postgres:latest
```

或手动触发：在 GitHub Actions 页面点击 "Run workflow"

---

### 方式二：本地多平台构建

**用于本地测试或手动发布**

#### 前置准备

```bash
# 1. 安装 buildx
docker buildx install

# 2. 创建多平台 builder
docker buildx create --name multiarch --use

# 3. 登录 Docker Hub
docker login
```

#### 使用构建脚本

```bash
# 构建所有镜像（不推送，仅本地测试）
./scripts/build-multiarch.sh

# 构建 Jing 镜像并推送到 Docker Hub
./scripts/build-multiarch.sh -j -P

# 构建 PostgreSQL 镜像并推送
./scripts/build-multiarch.sh -p -P

# 构建所有镜像，指定标签 v1.0.0，并推送
./scripts/build-multiarch.sh --all --push --tag v1.0.0

# 查看帮助
./scripts/build-multiarch.sh --help
```

---

### 方式三：单平台构建（开发测试）

**仅在当前平台构建，不支持多平台**

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

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！