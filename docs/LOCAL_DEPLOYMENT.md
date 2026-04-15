# Jing 本地部署指南（不使用 Docker）

本文档介绍如何在本地环境部署 Jing 项目，使用 Conda 管理 Python 环境。

---

## 一、环境要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Conda | Miniconda/Anaconda | Python 环境管理 |
| PostgreSQL | ≥ 16 + pgvector | 数据库（需要 pgvector 扩展） |
| Redis | ≥ 7.0 | 缓存服务 |
| 磁盘空间 | ≥ 2GB | 模型 + 依赖 |

---

## 二、详细部署步骤

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

### 5. 导入 SQL 脚本（按顺序）

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
REDIS_URL=redis://localhost:6379/0
```

#### Embedding 模型配置

```bash
LOCAL_EMBEDDING_ENABLED=true
LOCAL_EMBEDDING_MODEL_PATH=models/embedding
EMBEDDING_DIM=512
```

#### LLM API 配置（必需）

```bash
LLM_PROVIDER=cerebras
LLM_API_BASE_URL=http://your-llm-server:4000/v1
LLM_API_KEY=your_api_key
LLM_MODEL=qwen3-chat
```

#### DashScope API（必需，用于 ASR/TTS）

```bash
DASHSCOPE_API_KEY=your_dashscope_key
```

#### OpenClaw WebSocket（必需）

```bash
OPENCLAW_WS_URL=ws://localhost:18789/gateway
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

## 三、验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 就绪检查
curl http://localhost:8000/ready

# API 文档
open http://localhost:8000/docs
```

---

## 四、常见问题

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

---

## 五、SQL 文件说明

| 文件 | 说明 | 依赖 |
|------|------|------|
| `sql/init_db.sql` | 主数据库初始化，创建所有表 | pgvector, uuid-ossp |
| `sql/add_chinese_fts_columns.sql` | 添加中文全文搜索列 | zhparser |
| `sql/add_heartbeat_fts.sql` | 添加心跳事件 FTS 索引 | init_db.sql |

**导入顺序**：`init_db.sql` → `add_chinese_fts_columns.sql` → `add_heartbeat_fts.sql`

---

## 六、目录结构

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
└── scripts/
    └── download-embedding-model.sh  # 模型下载脚本