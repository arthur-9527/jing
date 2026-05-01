-- ============================================================
-- 中文分词 FTS 列（增量更新）
-- 执行方式：sudo -u postgres psql -d agent_backend -f 07_fts_columns.sql
-- ============================================================

-- ⚠️ 注意：此文件需要在 03_memory.sql 执行后运行
-- 因为需要依赖 chat_messages、key_events、heartbeat_events 表

-- ============================================================
-- 1. chat_messages 表中文分词列
-- ============================================================
ALTER TABLE chat_messages 
DROP COLUMN IF EXISTS content_tsv_cn;

ALTER TABLE chat_messages 
ADD COLUMN content_tsv_cn TSVECTOR GENERATED ALWAYS AS 
    (to_tsvector('chinese_zh', COALESCE(content, ''))) STORED;

-- 创建 GIN 索引
DROP INDEX IF EXISTS idx_chat_tsv_cn;
CREATE INDEX idx_chat_tsv_cn ON chat_messages USING GIN(content_tsv_cn);

-- ============================================================
-- 2. key_events 表中文分词列
-- ============================================================
ALTER TABLE key_events 
DROP COLUMN IF EXISTS content_tsv_cn;

ALTER TABLE key_events 
ADD COLUMN content_tsv_cn TSVECTOR GENERATED ALWAYS AS 
    (to_tsvector('chinese_zh', COALESCE(content, ''))) STORED;

-- 创建 GIN 索引
DROP INDEX IF EXISTS idx_key_events_tsv_cn;
CREATE INDEX idx_key_events_tsv_cn ON key_events USING GIN(content_tsv_cn);

-- ============================================================
-- 3. heartbeat_events 表中文分词列
-- ============================================================
-- 添加 simple 分词列（基础 FTS）
ALTER TABLE heartbeat_events 
ADD COLUMN IF NOT EXISTS trigger_text_tsv TSVECTOR GENERATED ALWAYS AS 
    (to_tsvector('simple', COALESCE(trigger_text, ''))) STORED;

-- 创建 GIN 索引
CREATE INDEX IF NOT EXISTS idx_heartbeat_tsv ON heartbeat_events USING GIN(trigger_text_tsv);

-- 添加中文分词列
ALTER TABLE heartbeat_events 
DROP COLUMN IF EXISTS trigger_text_tsv_cn;

ALTER TABLE heartbeat_events 
ADD COLUMN trigger_text_tsv_cn TSVECTOR GENERATED ALWAYS AS 
    (to_tsvector('chinese_zh', COALESCE(trigger_text, ''))) STORED;

-- 创建 GIN 索引
DROP INDEX IF EXISTS idx_heartbeat_tsv_cn;
CREATE INDEX idx_heartbeat_tsv_cn ON heartbeat_events USING GIN(trigger_text_tsv_cn);

-- ============================================================
-- 4. 验证
-- ============================================================
SELECT 'chinese_zh FTS columns created' AS status;

-- 测试分词效果（可选）
-- SELECT to_tsvector('chinese_zh', '我喜欢吃苹果');
-- 预期输出: '我':1 '喜欢':2 '吃':3 '苹果':4

\echo 'FTS 列创建完成: chat_messages, key_events, heartbeat_events'