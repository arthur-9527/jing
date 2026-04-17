-- ============================================================
-- MMD Agent Backend - 统一数据库初始化脚本
-- 合并 motion 表 + agent 表
-- 执行方式：sudo -u postgres psql -f init_db.sql
-- ============================================================

-- 1. 创建数据库
CREATE DATABASE agent_backend;

-- 2. 连接到数据库
\c agent_backend

-- 3. 启用扩展
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- Motion 相关表（1024 维向量）
-- ============================================================

-- 4. motions 表 (动作元数据)
CREATE TABLE motions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(255),
    description TEXT,
    original_fps INTEGER DEFAULT 30,
    original_frames INTEGER NOT NULL,
    original_duration FLOAT NOT NULL,
    keyframe_count INTEGER NOT NULL,
    is_loopable BOOLEAN DEFAULT FALSE,
    is_interruptible BOOLEAN DEFAULT TRUE,
    status VARCHAR(20) DEFAULT 'active',
    embedding VECTOR(1536),
    source_file VARCHAR(512),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_motions_name ON motions(name);
CREATE INDEX idx_motions_duration ON motions(original_duration);
CREATE INDEX idx_motions_status ON motions(status);
CREATE INDEX idx_motions_embedding ON motions USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 5. keyframes 表 (关键帧数据)
CREATE TABLE keyframes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motion_id UUID NOT NULL REFERENCES motions(id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    original_frame INTEGER NOT NULL,
    timestamp FLOAT NOT NULL,
    bone_data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(motion_id, frame_index)
);

CREATE INDEX idx_keyframes_motion ON keyframes(motion_id, frame_index);
CREATE INDEX idx_keyframes_timestamp ON keyframes(motion_id, timestamp);

-- 6. motion_tags 表 (标签字典)
CREATE TABLE motion_tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tag_type VARCHAR(50) NOT NULL,
    tag_name VARCHAR(100) NOT NULL,
    display_name VARCHAR(255),
    description TEXT,
    embedding VECTOR(1536),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tag_type, tag_name)
);

CREATE INDEX idx_motion_tags_type ON motion_tags(tag_type);
CREATE INDEX idx_motion_tags_name ON motion_tags(tag_name);

-- 7. motion_tag_map 表 (动作-标签关联)
CREATE TABLE motion_tag_map (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motion_id UUID NOT NULL REFERENCES motions(id) ON DELETE CASCADE,
    tag_id UUID NOT NULL REFERENCES motion_tags(id) ON DELETE CASCADE,
    weight FLOAT DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(motion_id, tag_id)
);

CREATE INDEX idx_motion_tag_map_motion ON motion_tag_map(motion_id);
CREATE INDEX idx_motion_tag_map_tag ON motion_tag_map(tag_id);

-- ============================================================
-- Agent 相关表（1536 维向量）
-- ============================================================

-- 8. character_background 表 (角色背景知识)
CREATE TABLE IF NOT EXISTS character_background (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cb_character_id ON character_background(character_id);
CREATE INDEX IF NOT EXISTS idx_cb_embedding ON character_background
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- 9. character_emotion 表 (角色情绪记忆)
CREATE TABLE IF NOT EXISTS character_emotion (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    inner_monologue TEXT NOT NULL,
    pad_delta JSONB NOT NULL,
    emotion_intensity FLOAT NOT NULL,
    trigger_keywords TEXT[] DEFAULT '{}',
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ce_character_user ON character_emotion(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_ce_embedding ON character_emotion
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- 10. user_history 表 (用户历史信息)
CREATE TABLE IF NOT EXISTS user_history (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    character_id VARCHAR(64) NOT NULL,
    info_type VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    embedding vector(1536),
    confidence FLOAT DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_uh_user_character ON user_history(user_id, character_id);
CREATE INDEX IF NOT EXISTS idx_uh_embedding ON user_history
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- 11. agent_state 表 (Agent 运行状态)
CREATE TABLE IF NOT EXISTS agent_state (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    pad_state JSONB NOT NULL,
    conversation_history JSONB DEFAULT '[]',
    turn_count INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id)
);

-- ============================================================
-- 权限配置
-- ============================================================
-- ⚠️ 注意：Docker 部署时，密码通过 POSTGRES_PASSWORD 环境变量配置
-- ⚠️ 手动部署时，请修改下面的密码为安全值

-- 12. 创建 admin 用户 (如不存在)
-- 密码占位符：请在执行前修改或在 .env 中配置 POSTGRES_PASSWORD
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'admin') THEN
        -- ⚠️ 请修改密码！建议使用环境变量或配置文件管理
        CREATE USER admin WITH PASSWORD 'PLEASE_CHANGE_THIS_PASSWORD';
        RAISE NOTICE '已创建 admin 用户，请立即修改密码！';
    END IF;
END
$$;

-- 13. 授予全部权限
GRANT CONNECT ON DATABASE agent_backend TO admin;
GRANT USAGE ON SCHEMA public TO admin;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO admin;

\echo '数据库初始化完成!'
\echo '数据库：agent_backend'
\echo '包含 motion 表 (4) + agent 表 (4) = 8 张表'
\echo ''
\echo '⚠️ 重要提示：'
\echo '  1. Docker 部署：密码由 POSTGRES_PASSWORD 环境变量控制'
\echo '  2. 手动部署：请执行 ALTER USER admin WITH PASSWORD ''你的密码'';'
