-- ============================================================
-- Agent 相关表（角色背景 + 情绪记忆 + 用户历史 + 运行状态）
-- 执行方式：sudo -u postgres psql -d agent_backend -f 02_agent.sql
-- ============================================================

-- ============================================================
-- 1. character_background 表 (角色背景知识)
-- ============================================================
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
    USING iviflat (embedding vector_cosine_ops) WITH (lists = 10);

-- ============================================================
-- 2. character_emotion 表 (角色情绪记忆)
-- ============================================================
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
    USING iviflat (embedding vector_cosine_ops) WITH (lists = 10);

-- ============================================================
-- 3. user_history 表 (用户历史信息)
-- ============================================================
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
    USING iviflat (embedding vector_cosine_ops) WITH (lists = 10);

-- ============================================================
-- 4. agent_state 表 (Agent 运行状态)
-- ============================================================
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

\echo 'Agent 表创建完成: character_background, character_emotion, user_history, agent_state'