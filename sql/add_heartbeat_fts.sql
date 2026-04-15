-- ===========================================
-- 为 heartbeat_events 表添加 FTS 索引
-- 执行日期: 2026-04-05
-- ===========================================

-- 添加 trigger_text_tsv 列（自动生成的 TSVector）
ALTER TABLE heartbeat_events 
ADD COLUMN IF NOT EXISTS trigger_text_tsv TSVECTOR GENERATED ALWAYS AS 
    (to_tsvector('simple', COALESCE(trigger_text, ''))) STORED;

-- 创建 GIN 索引
CREATE INDEX IF NOT EXISTS idx_heartbeat_tsv ON heartbeat_events USING GIN(trigger_text_tsv);

-- 验证
SELECT 'heartbeat_events FTS index created' AS status;