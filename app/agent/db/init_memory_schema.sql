-- ===========================================
-- 记忆系统表结构 (Memory System Schema)
-- 版本: v1.2
-- 创建日期: 2026-04-04
-- 更新日期: 2026-04-10 - 添加中文分词支持 (zhparser)
-- ===========================================

-- 确保 pgvector 扩展已启用
CREATE EXTENSION IF NOT EXISTS vector;

-- ===========================================
-- 中文分词配置 (zhparser)
-- ===========================================
-- 注意：需要先安装 scws 和 zhparser 扩展
-- 安装步骤：
-- 1. 安装 scws: wget http://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2 && tar xjf scws-1.2.3.tar.bz2 && cd scws-1.2.3 && ./configure && make && sudo make install
-- 2. 下载词典: wget http://www.xunsearch.com/scws/down/scws-dict-chs-utf8.tar.bz2 && tar xjf scws-dict-chs-utf8.tar.bz2 && sudo mv dict.utf8.xdb /usr/local/scws/etc/
-- 3. 安装 zhparser: git clone https://github.com/amutu/zhparser.git && cd zhparser && make SCWS_HOME=/usr/local && sudo make install SCWS_HOME=/usr/local
-- 4. 在 PostgreSQL 中执行: CREATE EXTENSION zhparser;

CREATE EXTENSION IF NOT EXISTS zhparser;
DROP TEXT SEARCH CONFIGURATION IF EXISTS chinese_zh;
CREATE TEXT SEARCH CONFIGURATION chinese_zh (PARSER = zhparser);
ALTER TEXT SEARCH CONFIGURATION chinese_zh ADD MAPPING FOR n,v,a,i,e,l WITH simple;

-- ===========================================
-- 1. 聊天记录表 (chat_messages)
-- ===========================================
-- 存储 raw 对话数据，保留14天后自动清理
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    role VARCHAR(16) NOT NULL,           -- 'user' / 'assistant'
    content TEXT NOT NULL,
    inner_monologue TEXT,                -- 内心独白（仅 assistant 角色有）
    turn_id BIGINT,                      -- 对话轮次ID（同一轮 user+assistant 共享）
    metadata JSONB DEFAULT '{}',         -- 其他附加信息
    content_tsv TSVECTOR GENERATED ALWAYS AS 
        (to_tsvector('simple', content)) STORED,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_extracted BOOLEAN DEFAULT FALSE,  -- 是否已被提取到关键事件/日记
    extracted_at TIMESTAMPTZ             -- 提取时间
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_chat_char_user_time ON chat_messages(character_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_content_tsv ON chat_messages USING GIN(content_tsv);
CREATE INDEX IF NOT EXISTS idx_chat_turn_id ON chat_messages(turn_id) WHERE turn_id IS NOT NULL;

-- ===========================================
-- 2. 关键事件表 (key_events)
-- ===========================================
-- 存储提取的用户关键信息，支持 PostgreSQL FTS
CREATE TABLE IF NOT EXISTS key_events (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(32) NOT NULL,     -- preference/fact/schedule/experience/emotion_trigger/initiative
    event_date DATE,                     -- 重要日期（生日、纪念日等）
    content TEXT NOT NULL,               -- 事件描述（纯文本）
    content_tsv TSVECTOR GENERATED ALWAYS AS 
        (to_tsvector('simple', content)) STORED,
    source_message_ids BIGINT[],         -- 来源消息ID列表
    importance FLOAT DEFAULT 0.5,        -- 重要性评分 (0-1)
    is_active BOOLEAN DEFAULT TRUE,      -- 是否有效（可失效）
    expires_at TIMESTAMPTZ,              -- 过期时间（日程类）
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_key_events_char_user ON key_events(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_key_events_type ON key_events(event_type);
CREATE INDEX IF NOT EXISTS idx_key_events_date ON key_events(event_date) WHERE event_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_key_events_tsv ON key_events USING GIN(content_tsv);
CREATE INDEX IF NOT EXISTS idx_key_events_active ON key_events(is_active) WHERE is_active = TRUE;

-- ===========================================
-- 3. 心动事件表 (heartbeat_events)
-- ===========================================
-- 存储情绪峰值、关系进展等特殊时刻
CREATE TABLE IF NOT EXISTS heartbeat_events (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    event_node VARCHAR(32) NOT NULL,     -- emotion_peak/relationship/user_reveal/special_moment
    event_subtype VARCHAR(32),           -- joy_peak/first_meeting/secret_reveal 等
    trigger_text TEXT NOT NULL,          -- 触发文本
    emotion_state JSONB NOT NULL,        -- PAD 状态快照 {"P": 0.3, "A": 0.5, "D": 0.6}
    intensity FLOAT NOT NULL,            -- 心动强度 (0-1)
    inner_monologue TEXT,                -- 内心独白
    source_message_id BIGINT,            -- 来源消息ID
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_heartbeat_char_user_time ON heartbeat_events(character_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_heartbeat_node ON heartbeat_events(event_node, event_subtype);
CREATE INDEX IF NOT EXISTS idx_heartbeat_intensity ON heartbeat_events(intensity DESC) WHERE intensity >= 0.5;

-- ===========================================
-- 4. 日记表 (daily_diary)
-- ===========================================
-- 每天生成的日记摘要，支持向量检索
CREATE TABLE IF NOT EXISTS daily_diary (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    diary_date DATE NOT NULL,
    summary TEXT NOT NULL,               -- 日记摘要（纯文本）
    embedding VECTOR(512),               -- 向量索引（用于检索）
    key_event_ids BIGINT[],              -- 关联的关键事件ID
    heartbeat_ids BIGINT[],              -- 关联的心动事件ID
    source_message_ids BIGINT[],         -- 来源消息ID范围
    mood_summary JSONB,                  -- 当日情绪总结 {"avg_P": 0.3, "avg_A": 0.5}
    highlight_count INT DEFAULT 0,       -- 高光时刻数量
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id, diary_date)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_diary_char_user_date ON daily_diary(character_id, user_id, diary_date DESC);
CREATE INDEX IF NOT EXISTS idx_diary_embedding ON daily_diary USING ivfflat(embedding vector_cosine_ops) WITH (lists = 10);

-- ===========================================
-- 5. 周索引表 (weekly_index)
-- ===========================================
-- 每7天生成的周摘要，支持向量检索
CREATE TABLE IF NOT EXISTS weekly_index (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    summary TEXT NOT NULL,
    embedding VECTOR(512),
    diary_ids BIGINT[],
    highlight_events JSONB,              -- 本周高光事件列表
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, user_id, week_start)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_weekly_char_user ON weekly_index(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_weekly_embedding ON weekly_index USING ivfflat(embedding vector_cosine_ops) WITH (lists = 10);

-- ===========================================
-- 6. 月索引表 (monthly_index)
-- ===========================================
-- 每月生成的月摘要，支持向量检索
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

-- 索引
CREATE INDEX IF NOT EXISTS idx_monthly_char_user ON monthly_index(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_monthly_embedding ON monthly_index USING ivfflat(embedding vector_cosine_ops) WITH (lists = 10);

-- ===========================================
-- 7. 年索引表 (annual_index)
-- ===========================================
-- 每年生成的年摘要，支持向量检索
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

-- 索引
CREATE INDEX IF NOT EXISTS idx_annual_char_user ON annual_index(character_id, user_id);
CREATE INDEX IF NOT EXISTS idx_annual_embedding ON annual_index USING ivfflat(embedding vector_cosine_ops) WITH (lists = 10);

-- ===========================================
-- 删除旧表（可选，谨慎执行）
-- ===========================================
-- 以下语句会删除旧表，请在确认数据不需要保留后执行
-- 注意：删除表会永久丢失数据！

-- DROP TABLE IF EXISTS character_emotion;
-- DROP TABLE IF EXISTS user_history;

-- ===========================================
-- 数据迁移脚本（可选）
-- ===========================================
-- 如果需要将旧表数据迁移到新表，请使用以下脚本

-- 迁移 user_history 到 key_events
-- INSERT INTO key_events (character_id, user_id, event_type, content, importance, created_at, updated_at)
-- SELECT 
--     character_id,
--     user_id,
--     CASE info_type
--         WHEN 'fact' THEN 'fact'
--         WHEN 'preference' THEN 'preference'
--         WHEN 'emotion' THEN 'emotion_trigger'
--         WHEN 'taboo' THEN 'preference'
--         ELSE 'fact'
--     END as event_type,
--     content,
--     confidence as importance,
--     created_at,
--     updated_at
-- FROM user_history;

-- 迁移 character_emotion 到 heartbeat_events
-- INSERT INTO heartbeat_events (character_id, user_id, event_node, trigger_text, emotion_state, intensity, inner_monologue, created_at)
-- SELECT 
--     character_id,
--     user_id,
--     'emotion_peak' as event_node,
--     COALESCE(trigger_keywords[1], '情绪事件') as trigger_text,
--     pad_delta as emotion_state,
--     emotion_intensity as intensity,
--     inner_monologue,
--     created_at
-- FROM character_emotion
-- WHERE emotion_intensity >= 0.3;