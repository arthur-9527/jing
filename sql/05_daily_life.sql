-- ============================================================
-- Daily Life 相关表（日常事务事件）
-- 执行方式：sudo -u postgres psql -d agent_backend -f 05_daily_life.sql
-- ============================================================

-- ============================================================
-- daily_life_events 表 (日常事务事件)
-- ============================================================
-- 存储角色自主活动的记录，作为日记素材
CREATE TABLE IF NOT EXISTS daily_life_events (
    id BIGSERIAL PRIMARY KEY,
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,          -- 事件发生时间
    scenario VARCHAR(128) NOT NULL,           -- 场景描述（如"逛街"、"做蛋糕"）
    scenario_detail TEXT,                      -- 场景详细描述
    dialogue TEXT,                             -- 角色说的话（1-2句）
    inner_monologue TEXT,                      -- 内心独白
    emotion_delta JSONB DEFAULT '{"P":0,"A":0,"D":0}',  -- 情绪变化
    emotion_state JSONB,                       -- 当时PAD快照
    intensity FLOAT DEFAULT 0.3,              -- 事件情绪强度 (0-1)
    heartbeat_event_id BIGINT,                -- 关联的心动事件ID（可选）
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_daily_life_char_user_time 
    ON daily_life_events(character_id, user_id, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_daily_life_scenario 
    ON daily_life_events(scenario);
CREATE INDEX IF NOT EXISTS idx_daily_life_heartbeat 
    ON daily_life_events(heartbeat_event_id) WHERE heartbeat_event_id IS NOT NULL;

-- 注释
COMMENT ON TABLE daily_life_events IS '角色日常事务事件表，记录自主活动作为日记素材';
COMMENT ON COLUMN daily_life_events.scenario IS '场景名称，如：逛街、做蛋糕、学跳舞';
COMMENT ON COLUMN daily_life_events.scenario_detail IS '场景详细描述，包含具体细节';
COMMENT ON COLUMN daily_life_events.dialogue IS '角色说的话，体现性格';
COMMENT ON COLUMN daily_life_events.inner_monologue IS '内心独白，真实想法';
COMMENT ON COLUMN daily_life_events.emotion_delta IS '情绪变化值 PAD';
COMMENT ON COLUMN daily_life_events.heartbeat_event_id IS '关联的心动事件ID';

\echo 'Daily Life 表创建完成: daily_life_events'