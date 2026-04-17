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

## 🚀 本地部署指南

本指南介绍如何在本地环境部署 Jing 项目，使用 Conda 管理 Python 环境。

### 环境要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Conda | Miniconda/Anaconda | Python 环境管理 |
| PostgreSQL | ≥ 16 + pgvector | 数据库（需要 pgvector 扩展） |
| Redis | ≥ 7.0 | 缓存服务 |
| 磁盘空间 | ≥ 2GB | 模型 + 依赖 |

---

### 详细部署步骤

#### 1. 创建 Conda 环境

```bash
conda create -n jing python=3.11 -y
conda activate jing
```

#### 2. 安装系统依赖

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y postgresql-16 postgresql-16-pgvector redis-server

# 启动服务
sudo systemctl start postgresql
sudo systemctl start redis-server
```

#### 3. 创建数据库和用户

```bash
sudo -u postgres psql << 'EOF'
CREATE USER admin WITH PASSWORD 'your_password';
CREATE DATABASE agent_backend OWNER admin;
\c agent_backend
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
EOF
```

#### 4. 安装 zhparser 中文分词

zhparser 是 PostgreSQL 中文分词扩展，用于聊天记录的全文搜索。

##### 4.1 安装 scws 分词引擎

```bash
cd /tmp
wget http://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2
tar -xjf scws-1.2.3.tar.bz2
cd scws-1.2.3
./configure
make
sudo make install
```

##### 4.2 下载 UTF-8 词典

项目使用 UTF-8 编码，需要下载 UTF-8 版本的词典：

```bash
cd /tmp
wget http://www.xunsearch.com/scws/down/scws-dict-chs-utf8.tar.bz2
tar -xjf scws-dict-chs-utf8.tar.bz2
sudo mkdir -p /usr/local/share/scws
sudo cp dict.utf8.xdb /usr/local/share/scws/
```

##### 4.3 编译安装 zhparser

```bash
cd /tmp
git clone https://github.com/amutu/zhparser.git
cd zhparser
export PATH=/usr/lib/postgresql/16/bin:$PATH
make
sudo make install
```

#### 5. 导入 SQL 脚本（按顺序）

```bash
# 设置密码环境变量
export PGPASSWORD=your_password

# 1. 主数据库初始化（创建所有表）
psql -U admin -d agent_backend -h localhost -f sql/init_db.sql

# 2. 中文分词列（需要 zhparser）
psql -U admin -d agent_backend -h localhost -f sql/add_chinese_fts_columns.sql

# 3. 心跳 FTS 索引
psql -U admin -d agent_backend -h localhost -f sql/add_heartbeat_fts.sql
```

#### 6. 安装 Python 依赖

```bash
conda activate jing
pip install -r requirements.txt

# 安装额外的 redis 包
pip install redis
```

#### 7. 下载 Embedding 模型

系统使用本地 Embedding 模型 `BAAI/bge-small-zh-v1.5`（约 100MB）：

```bash
./scripts/download-embedding-model.sh
```

模型将下载到 `models/embedding` 目录。

#### 8. 创建配置文件

复制项目中的 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

编辑 `.env` 文件，修改以下关键配置：

##### 数据库配置（本地部署需要修改）

```bash
# 数据库连接 URL（注意使用 localhost）
DATABASE_URL=postgresql+asyncpg://admin:your_password@localhost:5432/agent_backend

# Redis 连接 URL
REDIS_URL=redis://localhost:6379/0
```

##### Embedding 模型配置

```bash
LOCAL_EMBEDDING_ENABLED=true
LOCAL_EMBEDDING_MODEL_PATH=models/embedding
EMBEDDING_DIM=512
```

##### LLM API 配置（必需）

```bash
LLM_PROVIDER=cerebras
LLM_API_BASE_URL=http://your-llm-server:4000/v1
LLM_API_KEY=your_api_key
LLM_MODEL=qwen3-chat
```

##### DashScope API（必需，用于 ASR/TTS）

```bash
DASHSCOPE_API_KEY=your_dashscope_key
```

##### OpenClaw WebSocket（必需）

```bash
OPENCLAW_WS_URL=ws://localhost:18789/gateway
OPENCLAW_WS_TOKEN=your_token
```

#### 9. 启动服务

```bash
conda activate jing
python main.py
```

或使用 uvicorn：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

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
| `DATABASE_URL` | PostgreSQL 连接 URL | `postgresql+asyncpg://admin:password@localhost:5432/agent_backend` |
| `REDIS_URL` | Redis 连接 URL | `redis://localhost:6379/0` |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope API Key | `sk-xxx` |
| `LLM_API_BASE_URL` | LLM 服务地址 | `http://your-llm-server:4000/v1` |
| `LLM_API_KEY` | LLM API Key | `sk-xxx` |
| `OPENCLAW_WS_URL` | OpenClaw WebSocket 地址 | `ws://localhost:18789/gateway` |
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

## 📁 目录结构

```
jing/
├── .env                    # 配置文件（从 .env.example 复制）
├── .env.example            # 配置模板
├── main.py                 # 应用入口
├── requirements.txt        # Python 依赖
├── models/
│   └── embedding/          # Embedding 模型目录
├── config/
│   └── characters/         # 角色配置目录
│       └── daji/           # 默认角色
├── sql/
│   ├── init_db.sql         # 主数据库初始化
│   ├── add_chinese_fts_columns.sql  # 中文分词列
│   └── add_heartbeat_fts.sql        # 心跳 FTS 索引
├── scripts/
│   └── download-embedding-model.sh  # 模型下载脚本
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
├── tests/                   # 测试用例
├── logs/                    # 日志目录
├── temp/                    # 临时文件
├── dist/                    # 主前端构建输出
├── dist-vmd2sql/           # VMD 前端构建输出
├── Dockerfile              # Docker 镜像构建
├── docker-compose.yml      # 服务编排配置
├── start.sh                # 启动脚本
├── stop.sh                 # 停止脚本
└── requirements.txt        # Python 依赖
```

## 🎬 动作部署

> 📢 **动作数据来源声明**
>
> `example_motion/` 目录下的示例动作文件来源于开源项目 [MMDAgent](https://www.mmdagent.jp/)，
> 版权归原作者所有。使用前请遵守 MMDAgent 的相关许可协议。

### 动作系统概述

系统动作分为三类，根据不同场景自动触发：

| 分类 | 标签 | 用途 | 触发时机 |
|------|------|------|----------|
| 默认动作 | `system:default` | 低水位填充，循环播放 | 帧队列空闲时自动加载 |
| 空闲动作 | `system:idle` | 随机插入，丰富表现 | 20秒无输入后，每30-60秒随机插入 |
| 思考动作 | `system:thinking` | 展示倾听/思考姿态 | IDLE→LISTENING 状态转换时触发 |
| 普通动作 | `system:others` | LLM/API 触发使用 | 对话中被 Agent 调用 |

### 系统标签自动识别规则

上传动作时，AI 会根据**文本提示词**中的关键词自动识别并生成系统标签：

| 目标标签 | 提示词关键词组合 | 示例提示词 |
|----------|------------------|------------|
| `system:default` | "系统" + "默认" | "系统默认动作"、"系统默认待机" |
| `system:thinking` | "系统" + "思考" 或 "倾听" | "系统思考动作"、"系统倾听动作" |
| `system:idle` | "系统"（不含上述修饰词） | "系统动作"、"系统待机动作" |
| `system:others` | 不含"系统"二字 | "挥手动作"、"跳舞动作" |

> ⚠️ **重要提示**：提示词中必须包含"系统"二字才能被识别为系统动作，否则将被标记为普通动作（`system:others`）。

### vmd2sql 部署步骤

#### 1. 启动主服务

```bash
conda activate jing
python main.py
```

#### 2. 访问 VMD 上传页面

打开浏览器访问：`http://localhost:8000/vmd`

#### 3. 上传动作文件

- **VMD 文件**：选择 `.vmd` 格式的动作文件
- **预览视频**：上传动作预览视频（支持 MP4/MOV/AVI/WebM 格式）
- **文本提示词**：填写动作描述（用于 AI 生成标签）

#### 4. 确认并保存

- 检查 AI 生成的标签是否符合预期
- 可手动调整显示名称和标签
- 确认后保存入库

### 示例动作参考

`example_motion/` 目录提供的示例动作：

```
example_motion/
├── system_default/          # 默认动作（循环填充）
│   └── mei_wait.vmd         # 提示词建议："系统默认动作"
│
├── system_idle/             # 空闲动作（随机插入）
│   ├── mei_idle_boredom.vmd     # 提示词建议："系统动作 无聊"
│   ├── mei_idle_sleep.vmd       # 提示词建议："系统动作 睡觉"
│   ├── mei_idle_touch_clothes.vmd  # 提示词建议："系统动作 整理衣服"
│   └── mei_idle_yawn.vmd        # 提示词建议："系统动作 打哈欠"
│
├── system_thinking/         # 思考动作（倾听时触发）
│   ├── mei_idle_think.vmd   # 提示词建议："系统思考动作"
│   └── mei_flash.vmd        # 提示词建议："系统倾听动作 眼神闪烁"
│
└── others/                  # 普通动作（对话中使用）
    └── (预留目录)
```

---

## ❓ 常见问题

### PostgreSQL peer 认证失败

错误信息：`FATAL: Peer authentication failed for user "admin"`

**解决方案**：使用 `-h localhost` 参数通过 TCP 连接，避免 Unix socket 的 peer 认证：

```bash
psql -U admin -d agent_backend -h localhost -f sql/init_db.sql
```

### 缺少 redis Python 包

错误信息：`ModuleNotFoundError: No module named 'redis'`

**解决方案**：

```bash
pip install redis
```

### content_tsv_cn 列不存在

错误信息：`column "content_tsv_cn" does not exist`

**解决方案**：需要先安装 zhparser 中文分词扩展，然后执行 `sql/add_chinese_fts_columns.sql`：

```bash
# 确认 zhparser 已安装
psql -U admin -d agent_backend -h localhost -c "SELECT * FROM pg_extension WHERE extname='zhparser';"

# 执行中文分词列 SQL
psql -U admin -d agent_backend -h localhost -f sql/add_chinese_fts_columns.sql
```

### zhparser 安装失败

确保：
1. scws 已正确安装（`scws -h` 可查看帮助）
2. UTF-8 词典已放置到 `/usr/local/share/scws/`
3. PostgreSQL 开发包已安装（`postgresql-server-dev-16`）

### Embedding 模型加载失败

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

### 数据库连接失败

**原因**：PostgreSQL 未就绪或密码不匹配。

**解决**：
```bash
# 检查 PostgreSQL 状态
sudo systemctl status postgresql

# 确认密码一致
grep DATABASE_URL .env

# 重启数据库
sudo systemctl restart postgresql
```

### OpenClaw WebSocket 连接失败

**原因**：OpenClaw 服务未启动或地址配置错误。

**解决**：
```bash
# 检查 OpenClaw 服务状态
curl http://localhost:18789/health

# 检查配置
grep OPENCLAW_WS .env
```

### ASR/TTS 调用失败

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

### 服务启动后健康检查失败

**原因**：依赖服务未就绪。

**解决**：
```bash
# 查看详细日志
# 根据启动方式查看日志

# 检查所有服务状态
sudo systemctl status postgresql
sudo systemctl status redis-server

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

## 📄 SQL 文件说明

| 文件 | 说明 | 依赖 |
|------|------|------|
| `sql/init_db.sql` | 主数据库初始化，创建所有表 | pgvector, uuid-ossp |
| `sql/add_chinese_fts_columns.sql` | 添加中文全文搜索列 | zhparser |
| `sql/add_heartbeat_fts.sql` | 添加心跳事件 FTS 索引 | init_db.sql |

**导入顺序**：`init_db.sql` → `add_chinese_fts_columns.sql` → `add_heartbeat_fts.sql`

## 📄 License

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！