-- ============================================================
-- Memory 相关表（聊天记录 + 关键事件 + 心动事件 + 日记 + 索引）
-- 执行方式：sudo -u postgres psql -d agent_backend -f 03_memory.sql
-- ============================================================

-- ============================================================
-- 1. chat_messages 表 (聊天记录)
-- ============================================================
-- 存储 raw 对话数据，保留14天后自动清理
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    role VARCHAR(16) NOT NULL,  -- 'user' / 'assistant'
    content TEXT NOT NULL,
    inner_monologue TEXT,  -- 内心独白（仅 assistant 角色有）
    turn_id BIGINT,  -- 对话轮次ID
    metadata JSONB DEFAULT '{}',
    content_tsv TSVECTOR,  -- FTS 向量（由数据库生成）
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_extracted BOOLEAN DEFAULT FALSE,  -- 是否已被提取
    extracted_at TIMESTAMPTZ  -- 提取时间
);

CREATE INDEX IF NOT EXISTS idx_chat_character_user ON chat_messages(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_chat_created_at ON chat_messages(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_role ON chat_messages(role);
CREATE INDEX IF NOT EXISTS idx_chat_tsv ON chat_messages USING GIN(content_tsv);

-- ============================================================
-- 2. key_events 表 (关键事件)
-- ============================================================
-- 存储提取的用户关键信息，支持 PostgreSQL FTS
CREATE TABLE IF NOT EXISTS key_events (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(32) NOT NULL,  -- preference/fact/schedule/experience/emotion_trigger/initiative
    event_date DATE,  -- 重要日期（生日、纪念日等）
    content TEXT NOT NULL,
    content_tsv TSVECTOR,  -- FTS 向量
    source_message_ids BIGINT[],  -- 来源消息ID列表
    importance FLOAT DEFAULT 0.5,  -- 重要性评分 (0-1)
    is_active BOOLEAN DEFAULT TRUE,  -- 是否有效
    expires_at TIMESTAMPTZ,  -- 过期时间（日程类）
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_key_events_character_user ON key_events(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_key_events_type ON key_events(event_type);
CREATE INDEX IF NOT EXISTS idx_key_events_importance ON key_events(importance DESC);
CREATE INDEX IF NOT EXISTS idx_key_events_tsv ON key_events USING GIN(content_tsv);

-- ============================================================
-- 3. heartbeat_events 表 (心动事件)
-- ============================================================
-- 存储情绪峰值、关系进展等特殊时刻
CREATE TABLE IF NOT EXISTS heartbeat_events (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    event_node VARCHAR(32) NOT NULL,  -- emotion_peak/relationship/user_reveal/special_moment
    event_subtype VARCHAR(32),  -- joy_peak/first_meeting/secret_reveal 等
    trigger_text TEXT NOT NULL,
    emotion_state JSONB NOT NULL,  -- PAD 状态快照
    intensity FLOAT NOT NULL,  -- 心动强度 (0-1)
    inner_monologue TEXT,
    source_message_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_heartbeat_character_user ON heartbeat_events(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_heartbeat_node ON heartbeat_events(event_node);
CREATE INDEX IF NOT EXISTS idx_heartbeat_intensity ON heartbeat_events(intensity DESC);
CREATE INDEX IF NOT EXISTS idx_heartbeat_created_at ON heartbeat_events(created_at DESC);

-- ============================================================
-- 4. daily_diary 表 (日记)
-- ============================================================
-- 每天生成的日记摘要，支持向量检索
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
    mood_summary JSONB,  -- {"avg_P": 0.3, "avg_A": 0.5}
    highlight_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id, diary_date)
);

CREATE INDEX IF NOT EXISTS idx_diary_character_user ON daily_diary(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_diary_date ON daily_diary(diary_date DESC);
CREATE INDEX IF NOT EXISTS idx_diary_embedding ON daily_diary USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- ============================================================
-- 5. weekly_index 表 (周索引)
-- ============================================================
-- 每7天生成的周摘要
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

CREATE INDEX IF NOT EXISTS idx_weekly_character_user ON weekly_index(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_weekly_embedding ON weekly_index USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- ============================================================
-- 6. monthly_index 表 (月索引)
-- ============================================================
-- 每月生成的月摘要
CREATE TABLE IF NOT EXISTS monthly_index (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    summary TEXT NOT NULL,
    embedding VECTOR(512),
    weekly_ids BIGINT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id, year, month)
);

CREATE INDEX IF NOT EXISTS idx_monthly_character_user ON monthly_index(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_monthly_embedding ON monthly_index USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- ============================================================
-- 7. annual_index 表 (年索引)
-- ============================================================
-- 每年生成的年摘要
CREATE TABLE IF NOT EXISTS annual_index (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    year INTEGER NOT NULL,
    summary TEXT NOT NULL,
    embedding VECTOR(512),
    monthly_ids BIGINT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id, year)
);

CREATE INDEX IF NOT EXISTS idx_annual_character_user ON annual_index(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_annual_embedding ON annual_index USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

\echo 'Memory 表创建完成: chat_messages, key_events, heartbeat_events, daily_diary, weekly_index, monthly_index, annual_index'