-- ============================================================
-- Jing Database - 统一初始化脚本
-- 版本: v2.0
-- Embedding 维度: 512 (bge-small-zh-v1.5)
-- ============================================================
-- 说明：
--   1. 本脚本不创建数据库和用户（由 Docker 环境变量控制）
--   2. 用户通过 POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB 配置
--   3. PostgreSQL 官方镜像会自动创建用户和数据库
--   4. 启动时自动执行本脚本初始化表结构
-- ============================================================

-- ===========================================
-- 1. 启用扩展
-- ===========================================
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- zhparser 中文分词扩展（如已安装）
CREATE EXTENSION IF NOT EXISTS zhparser;

-- ===========================================
-- 2. 配置中文分词 (zhparser)
-- ===========================================
-- 如果 zhparser 已安装，创建中文分词配置
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'zhparser') THEN
        DROP TEXT SEARCH CONFIGURATION IF EXISTS chinese_zh CASCADE;
        CREATE TEXT SEARCH CONFIGURATION chinese_zh (PARSER = zhparser);
        ALTER TEXT SEARCH CONFIGURATION chinese_zh ADD MAPPING FOR n,v,a,i,e,l WITH simple;
        RAISE NOTICE '中文分词配置 chinese_zh 已创建';
    ELSE
        RAISE NOTICE 'zhparser 未安装，跳过中文分词配置';
    END IF;
END
$$;

-- ============================================================
-- Motion 相关表（动作管理系统）
-- ============================================================

-- ===========================================
-- 3. motions 表 (动作元数据)
-- ===========================================
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
    embedding VECTOR(512),
    source_file VARCHAR(512),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_motions_name ON motions(name);
CREATE INDEX idx_motions_duration ON motions(original_duration);
CREATE INDEX idx_motions_status ON motions(status);
CREATE INDEX idx_motions_embedding ON motions USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ===========================================
-- 4. keyframes 表 (关键帧数据)
-- ===========================================
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

-- ===========================================
-- 5. motion_tags 表 (标签字典)
-- ===========================================
CREATE TABLE motion_tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tag_type VARCHAR(50) NOT NULL,
    tag_name VARCHAR(100) NOT NULL,
    display_name VARCHAR(255),
    description TEXT,
    embedding VECTOR(512),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tag_type, tag_name)
);

CREATE INDEX idx_motion_tags_type ON motion_tags(tag_type);
CREATE INDEX idx_motion_tags_name ON motion_tags(tag_name);

-- ===========================================
-- 6. motion_tag_map 表 (动作-标签关联)
-- ===========================================
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
-- Agent 相关表（角色状态管理）
-- ============================================================

-- ===========================================
-- 7. character_background 表 (角色背景知识)
-- ===========================================
CREATE TABLE IF NOT EXISTS character_background (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding VECTOR(512),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cb_character_id ON character_background(character_id);
CREATE INDEX IF NOT EXISTS idx_cb_embedding ON character_background
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- ===========================================
-- 8. agent_state 表 (Agent 运行状态)
-- ===========================================
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
-- Memory 相关表（记忆系统）
-- ============================================================

-- ===========================================
-- 9. chat_messages 表 (聊天记录)
-- ===========================================
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    inner_monologue TEXT,
    turn_id BIGINT,
    metadata JSONB DEFAULT '{}',
    content_tsv TSVECTOR GENERATED ALWAYS AS 
        (to_tsvector('simple', content)) STORED,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_extracted BOOLEAN DEFAULT FALSE,
    extracted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_chat_char_user_time ON chat_messages(character_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_content_tsv ON chat_messages USING GIN(content_tsv);
CREATE INDEX IF NOT EXISTS idx_chat_turn_id ON chat_messages(turn_id) WHERE turn_id IS NOT NULL;

-- ===========================================
-- 10. key_events 表 (关键事件)
-- ===========================================
CREATE TABLE IF NOT EXISTS key_events (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    event_date DATE,
    content TEXT NOT NULL,
    content_tsv TSVECTOR GENERATED ALWAYS AS 
        (to_tsvector('simple', content)) STORED,
    source_message_ids BIGINT[],
    importance FLOAT DEFAULT 0.5,
    is_active BOOLEAN DEFAULT TRUE,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_key_events_char_user ON key_events(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_key_events_type ON key_events(event_type);
CREATE INDEX IF NOT EXISTS idx_key_events_date ON key_events(event_date) WHERE event_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_key_events_tsv ON key_events USING GIN(content_tsv);
CREATE INDEX IF NOT EXISTS idx_key_events_active ON key_events(is_active) WHERE is_active = TRUE;

-- ===========================================
-- 11. heartbeat_events 表 (心动事件)
-- ===========================================
CREATE TABLE IF NOT EXISTS heartbeat_events (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    event_node VARCHAR(32) NOT NULL,
    event_subtype VARCHAR(32),
    trigger_text TEXT NOT NULL,
    emotion_state JSONB NOT NULL,
    intensity FLOAT NOT NULL,
    inner_monologue TEXT,
    source_message_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_heartbeat_char_user_time ON heartbeat_events(character_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_heartbeat_node ON heartbeat_events(event_node, event_subtype);
CREATE INDEX IF NOT EXISTS idx_heartbeat_intensity ON heartbeat_events(intensity DESC) WHERE intensity >= 0.5;

-- ===========================================
-- 12. daily_diary 表 (日记摘要)
-- ===========================================
CREATE TABLE IF NOT EXISTS daily_diary (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    diary_date DATE NOT NULL,
    summary TEXT NOT NULL,
    embedding VECTOR(512),
    key_event_ids BIGINT[],
    heartbeat_ids BIGINT[],
    source_message_ids BIGINT[],
    mood_summary JSONB,
    highlight_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id, diary_date)
);

CREATE INDEX IF NOT EXISTS idx_diary_char_user_date ON daily_diary(character_id, user_id, diary_date DESC);
CREATE INDEX IF NOT EXISTS idx_diary_embedding ON daily_diary USING ivfflat(embedding vector_cosine_ops) WITH (lists = 10);

-- ===========================================
-- 13. weekly_index 表 (周索引)
-- ===========================================
CREATE TABLE IF NOT EXISTS weekly_index (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    summary TEXT NOT NULL,
    embedding VECTOR(512),
    diary_ids BIGINT[],
    highlight_events JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id, week_start)
);

CREATE INDEX IF NOT EXISTS idx_weekly_char_user ON weekly_index(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_weekly_embedding ON weekly_index USING ivfflat(embedding vector_cosine_ops) WITH (lists = 10);

-- ===========================================
-- 14. monthly_index 表 (月索引)
-- ===========================================
CREATE TABLE IF NOT EXISTS monthly_index (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    year INT NOT NULL,
    month INT NOT NULL,
    summary TEXT NOT NULL,
    embedding VECTOR(512),
    weekly_ids BIGINT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id, year, month)
);

CREATE INDEX IF NOT EXISTS idx_monthly_char_user ON monthly_index(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_monthly_embedding ON monthly_index USING ivfflat(embedding vector_cosine_ops) WITH (lists = 10);

-- ===========================================
-- 15. annual_index 表 (年索引)
-- ===========================================
CREATE TABLE IF NOT EXISTS annual_index (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    year INT NOT NULL,
    summary TEXT NOT NULL,
    embedding VECTOR(512),
    monthly_ids BIGINT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id, year)
);

CREATE INDEX IF NOT EXISTS idx_annual_char_user ON annual_index(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_annual_embedding ON annual_index USING ivfflat(embedding vector_cosine_ops) WITH (lists = 10);

-- ============================================================
-- 完成
-- ============================================================
\echo '============================================'
\echo 'Jing 数据库初始化完成!'
\echo '============================================'
\echo '包含表:'
\echo '  - Motion 系统: motions, keyframes, motion_tags, motion_tag_map'
\echo '  - Agent 系统: character_background, agent_state'
\echo '  - Memory 系统: chat_messages, key_events, heartbeat_events,'
\echo '                 daily_diary, weekly_index, monthly_index, annual_index'
\echo ''
\echo 'Embedding 维度: 512 (bge-small-zh-v1.5)'
\echo ''
\echo '注意: 用户权限由 Docker POSTGRES_USER 环境变量控制'
\echo '============================================'