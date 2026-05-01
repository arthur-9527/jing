-- ============================================================
-- Affection 相关表（好感度系统）
-- 执行方式：sudo -u postgres psql -d agent_backend -f 04_affection.sql
-- ============================================================

-- ============================================================
-- affection_state 表 (好感度状态)
-- ============================================================
-- 极简设计：只存储三维数值，不记录变化历史
CREATE TABLE IF NOT EXISTS affection_state (
    character_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    
    -- 三维基础好感度（永久存储）
    trust_base FLOAT DEFAULT 0.0 CHECK (trust_base >= -100 AND trust_base <= 100),
    intimacy_base FLOAT DEFAULT 0.0 CHECK (intimacy_base >= -100 AND intimacy_base <= 100),
    respect_base FLOAT DEFAULT 0.0 CHECK (respect_base >= -100 AND respect_base <= 100),
    
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(character_id, user_id)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_affection_character_user ON affection_state(character_id, user_id);

-- 更新时间触发器
CREATE OR REPLACE FUNCTION update_affection_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_affection_update_time
BEFORE UPDATE ON affection_state
FOR EACH ROW EXECUTE FUNCTION update_affection_timestamp();

\echo 'Affection 表创建完成: affection_state'