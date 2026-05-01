# Jing 本地部署指南

本指南介绍如何在本地环境部署 Jing 项目，使用 Conda 管理 Python 环境。

## 环境要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Conda | Miniconda/Anaconda | Python 环境管理 |
| PostgreSQL | ≥ 16 + pgvector | 数据库（需要 pgvector 扩展） |
| Redis | ≥ 7.0 | 缓存服务 |
| 磁盘空间 | ≥ 2GB | 模型 + 依赖 |

---

## 详细部署步骤

### 1. 创建 Conda 环境

```bash
conda create -n jing python=3.11 -y
conda activate jing
```

### 2. 安装系统依赖

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y postgresql-16 postgresql-16-pgvector redis-server

# 启动服务
sudo systemctl start postgresql
sudo systemctl start redis-server
```

### 3. 创建数据库和用户

```bash
sudo -u postgres psql << 'EOF'
CREATE USER admin WITH PASSWORD 'your_password';
CREATE DATABASE agent_backend OWNER admin;
\c agent_backend
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
EOF
```

### 4. 安装 zhparser 中文分词

zhparser 是 PostgreSQL 中文分词扩展，用于聊天记录的全文搜索。

#### 4.1 安装 scws 分词引擎

```bash
cd /tmp
wget http://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2
tar -xjf scws-1.2.3.tar.bz2
cd scws-1.2.3
./configure
make
sudo make install
```

#### 4.2 下载 UTF-8 词典

项目使用 UTF-8 编码，需要下载 UTF-8 版本的词典：

```bash
cd /tmp
wget http://www.xunsearch.com/scws/down/scws-dict-chs-utf8.tar.bz2
tar -xjf scws-dict-chs-utf8.tar.bz2
sudo mkdir -p /usr/local/share/scws
sudo cp dict.utf8.xdb /usr/local/share/scws/
```

#### 4.3 编译安装 zhparser

```bash
cd /tmp
git clone https://github.com/amutu/zhparser.git
cd zhparser
export PATH=/usr/lib/postgresql/16/bin:$PATH
make
sudo make install
```

### 5. 导入 SQL 脚本

数据库表结构已拆分为模块化 SQL 文件，可使用统一入口一键初始化：

```bash
# 设置密码环境变量
export PGPASSWORD=your_password

# 统一初始化（推荐，自动按顺序加载所有 SQL 文件）
sudo -u postgres psql -f sql/init_all.sql

# 或者逐文件导入：
# psql -U admin -d agent_backend -h localhost -f sql/00_extensions.sql
# psql -U admin -d agent_backend -h localhost -f sql/01_motion.sql
# psql -U admin -d agent_backend -h localhost -f sql/02_agent.sql
# psql -U admin -d agent_backend -h localhost -f sql/03_memory.sql
# psql -U admin -d agent_backend -h localhost -f sql/04_affection.sql
# psql -U admin -d agent_backend -h localhost -f sql/05_daily_life.sql
# psql -U admin -d agent_backend -h localhost -f sql/06_im_channel.sql
# psql -U admin -d agent_backend -h localhost -f sql/07_fts_columns.sql
```

> `init_all.sql` 会自动连接 `agent_backend` 数据库并依次加载 00-07 共 8 个 SQL 文件，创建包含 Motion、Agent、Memory、Affection、Daily Life、IM Channel 在内的 19 张表。

### 6. 安装 Python 依赖

```bash
conda activate jing
pip install -r requirements.txt

# 安装额外的 redis 包
pip install redis
```

### 7. 下载 Embedding 模型

系统使用本地 Embedding 模型 `BAAI/bge-small-zh-v1.5`（约 100MB）：

```bash
./scripts/download-embedding-model.sh
```

模型将下载到 `models/embedding` 目录。

### 8. 创建配置文件

复制项目中的 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

编辑 `.env` 文件，修改以下关键配置：

#### 数据库配置（本地部署需要修改）

```bash
# 数据库连接 URL（注意使用 localhost）
DATABASE_URL=postgresql+asyncpg://admin:your_password@localhost:5432/agent_backend

# Redis 连接 URL
REDIS_URL=redis://localhost:6379/1
```

#### Embedding 模型配置

```bash
LOCAL_EMBEDDING_MODEL_PATH=./models/embedding
EMBEDDING_DIM=512
```

#### LLM Provider 配置（必需）

```bash
# LiteLLM Provider（OpenAI 兼容 API）
LITELLM_API_BASE_URL=http://your-llm-server:4000/v1
LITELLM_API_KEY=your_api_key
LITELLM_MODEL=qwen3-chat

# Cerebras Provider（可选）
CEREBRAS_API_BASE_URL=http://your-llm-server:4000
CEREBRAS_API_KEY=your_api_key
CEREBRAS_MODEL=qwen3-chat

# 服务 Provider 选择器
CHAT_PROVIDER=litellm
VISION_PROVIDER=litellm
VISION_MODEL=qwen-vl-plus
```

#### DashScope API（必需，用于 ASR/TTS）

```bash
DASHSCOPE_API_KEY=your_dashscope_key
```

#### OpenClaw WebSocket（必需）

```bash
OPENCLAW_WS_URL=ws://127.0.0.1:18789/gateway
OPENCLAW_WS_TOKEN=your_token
```

### 9. 启动服务

```bash
conda activate jing
python main.py
```

或使用 uvicorn：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 就绪检查
curl http://localhost:8000/ready

# API 文档
open http://localhost:8000/docs
```

---

## 配置说明

配置文件：`.env`（从 `.env.example` 复制）

### 必需配置项

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `DATABASE_URL` | PostgreSQL 连接 URL | `postgresql+asyncpg://admin:password@localhost:5432/agent_backend` |
| `REDIS_URL` | Redis 连接 URL | `redis://localhost:6379/1` |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope API Key | `sk-xxx` |
| `LITELLM_API_BASE_URL` | LiteLLM 服务地址 | `http://your-llm-server:4000/v1` |
| `LITELLM_API_KEY` | LiteLLM API Key | `sk-xxx` |
| `OPENCLAW_WS_URL` | OpenClaw WebSocket 地址 | `ws://127.0.0.1:18789/gateway` |
| `OPENCLAW_WS_TOKEN` | OpenClaw WebSocket Token | `your_token` |

### 可选配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `CHAT_PROVIDER` | `litellm` | 对话 LLM 提供商 (`litellm` / `cerebras`) |
| `LITELLM_MODEL` | `qwen3-chat` | LiteLLM 模型名称 |
| `THINKING_PROVIDER` | `litellm` | 思考服务 LLM 提供商 |
| `VISION_PROVIDER` | `litellm` | 视觉服务提供商 |
| `VISION_MODEL` | `qwen-vl-plus` | 视觉模型名称 |
| `ASR_PROVIDER` | `qwen` | ASR 提供商 (`qwen` / `deepgram`) |
| `TTS_PROVIDER` | `cosyvoice` | TTS 提供商 (`cosyvoice` / `cartesia`) |
| `POST_PROCESS_ENABLED` | `true` | 是否启用回复二次改写 |
| `SCHEDULER_ENABLED` | `true` | 是否启用定时任务调度 |
| `DAILY_LIFE_ENABLED` | `true` | 是否启用每日生活系统 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `PORT` | `8000` | 服务端口 |

### Embedding 模型配置

系统使用本地 Embedding 模型，需要预先下载：

```bash
# 自动下载 BAAI/bge-small-zh-v1.5 模型（约 100MB）
./scripts/download-embedding-model.sh

# 模型将保存到 ./models/embedding 目录
```

### QQ Bot 配置

如需启用 QQ Bot 功能，在 `.env` 中配置：

```bash
QQ_BOT_APPID=your_qq_bot_appid
QQ_BOT_SECRET=your_qq_bot_secret
```

### 每日生活配置

角色自主行为系统，启用后角色会根据时间段主动发起问候和行为：

```bash
DAILY_LIFE_ENABLED=true
```

事件配置位于 `app/daily_life/` 模块中。

### 图片/视频生成配置

支持通过 DashScope 进行图生图和图生视频：

```bash
IMAGE_VIDEO_GEN_ENABLED=false
IMAGE_GEN_PROVIDER=dashscope
VIDEO_GEN_PROVIDER=dashscope
DASHSCOPE_IMAGE_GEN_MODEL=wanx2.1-t2i-plus
DASHSCOPE_VIDEO_GEN_MODEL=wanx2.1-i2v-plus
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

---

## 动作部署

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

## 常见问题

### PostgreSQL peer 认证失败

错误信息：`FATAL: Peer authentication failed for user "admin"`

**解决方案**：使用 `-h localhost` 参数通过 TCP 连接，避免 Unix socket 的 peer 认证：

```bash
psql -U admin -d agent_backend -h localhost -f sql/init_all.sql
```

### 缺少 redis Python 包

错误信息：`ModuleNotFoundError: No module named 'redis'`

**解决方案**：

```bash
pip install redis
```

### content_tsv_cn 列不存在

错误信息：`column "content_tsv_cn" does not exist`

**解决方案**：需要先安装 zhparser 中文分词扩展，然后执行 `sql/07_fts_columns.sql`：

```bash
# 确认 zhparser 已安装
psql -U admin -d agent_backend -h localhost -c "SELECT * FROM pg_extension WHERE extname='zhparser';"

# 执行中文分词列 SQL
psql -U admin -d agent_backend -h localhost -f sql/07_fts_columns.sql
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

---

## 开发指南

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

---

## SQL 文件说明

| 文件 | 说明 | 依赖 |
|------|------|------|
| `sql/00_extensions.sql` | 安装 pgvector, uuid-ossp, zhparser 扩展 | PostgreSQL 16+ |
| `sql/01_motion.sql` | Motion 动作相关表（motions, keyframes, tags） | 00_extensions |
| `sql/02_agent.sql` | Agent 角色相关表（background, emotion, state） | 00_extensions |
| `sql/03_memory.sql` | Memory 记忆层级表（chat, events, diary, indexes） | 00_extensions |
| `sql/04_affection.sql` | Affection 好感度状态表 | 00_extensions |
| `sql/05_daily_life.sql` | Daily Life 每日生活事件表 | 00_extensions |
| `sql/06_im_channel.sql` | IM Channel 用户和平台绑定表 | 00_extensions |
| `sql/07_fts_columns.sql` | 中文全文搜索列（需要 zhparser） | 03_memory |
| `sql/init_all.sql` | 统一入口，自动按顺序加载以上所有文件 | - |

**导入方式**：直接使用 `sudo -u postgres psql -f sql/init_all.sql` 一键初始化，或按编号顺序逐文件导入。