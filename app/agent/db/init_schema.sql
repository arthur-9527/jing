-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- 角色背景知识表
CREATE TABLE IF NOT EXISTS character_background (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(512),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cb_character_id ON character_background(character_id);
CREATE INDEX IF NOT EXISTS idx_cb_embedding ON character_background
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- 角色情绪记忆表
CREATE TABLE IF NOT EXISTS character_emotion (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    inner_monologue TEXT NOT NULL,
    pad_delta JSONB NOT NULL,           -- {"P": 0.1, "A": -0.05, "D": 0.0}
    emotion_intensity FLOAT NOT NULL,
    trigger_keywords TEXT[] DEFAULT '{}',
    embedding vector(512),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ce_character_user ON character_emotion(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_ce_embedding ON character_emotion
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- 用户历史信息表
CREATE TABLE IF NOT EXISTS user_history (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    character_id VARCHAR(64) NOT NULL,
    info_type VARCHAR(32) NOT NULL,     -- fact / emotion / preference / taboo
    content TEXT NOT NULL,
    embedding vector(512),
    confidence FLOAT DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_uh_user_character ON user_history(user_id, character_id);
CREATE INDEX IF NOT EXISTS idx_uh_embedding ON user_history
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- Agent 运行状态表
CREATE TABLE IF NOT EXISTS agent_state (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    pad_state JSONB NOT NULL,           -- {"P": 0.3, "A": 0.5, "D": 0.6}
    conversation_history JSONB DEFAULT '[]',
    turn_count INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id)
);
