-- ===========================================
-- 添加中文分词 FTS 列 (zhparser)
-- 执行日期: 2026-04-10
-- ===========================================

-- 确保 zhparser 扩展和中文配置已创建
CREATE EXTENSION IF NOT EXISTS zhparser;
DROP TEXT SEARCH CONFIGURATION IF EXISTS chinese_zh CASCADE;
CREATE TEXT SEARCH CONFIGURATION chinese_zh (PARSER = zhparser);
ALTER TEXT SEARCH CONFIGURATION chinese_zh ADD MAPPING FOR n,v,a,i,e,l WITH simple;

-- ===========================================
-- 1. chat_messages 表
-- ===========================================
-- 删除旧的 simple 分词列（如果需要保留兼容性，可以同时保留两列）
-- 注意：GENERATED 列不能直接修改，需要先删除再添加

-- 添加中文分词列
ALTER TABLE chat_messages 
DROP COLUMN IF EXISTS content_tsv_cn;

ALTER TABLE chat_messages 
ADD COLUMN content_tsv_cn TSVECTOR GENERATED ALWAYS AS 
    (to_tsvector('chinese_zh', COALESCE(content, ''))) STORED;

-- 创建 GIN 索引
DROP INDEX IF EXISTS idx_chat_tsv_cn;
CREATE INDEX idx_chat_tsv_cn ON chat_messages USING GIN(content_tsv_cn);

-- ===========================================
-- 2. key_events 表
-- ===========================================
ALTER TABLE key_events 
DROP COLUMN IF EXISTS content_tsv_cn;

ALTER TABLE key_events 
ADD COLUMN content_tsv_cn TSVECTOR GENERATED ALWAYS AS 
    (to_tsvector('chinese_zh', COALESCE(content, ''))) STORED;

-- 创建 GIN 索引
DROP INDEX IF EXISTS idx_key_events_tsv_cn;
CREATE INDEX idx_key_events_tsv_cn ON key_events USING GIN(content_tsv_cn);

-- ===========================================
-- 3. heartbeat_events 表
-- ===========================================
ALTER TABLE heartbeat_events 
DROP COLUMN IF EXISTS trigger_text_tsv_cn;

ALTER TABLE heartbeat_events 
ADD COLUMN trigger_text_tsv_cn TSVECTOR GENERATED ALWAYS AS 
    (to_tsvector('chinese_zh', COALESCE(trigger_text, ''))) STORED;

-- 创建 GIN 索引
DROP INDEX IF EXISTS idx_heartbeat_tsv_cn;
CREATE INDEX idx_heartbeat_tsv_cn ON heartbeat_events USING GIN(trigger_text_tsv_cn);

-- ===========================================
-- 4. 验证
-- ===========================================
-- 验证 zhparser 分词效果
SELECT 'chinese_zh FTS columns created' AS status;

-- 测试分词效果（可选）
-- SELECT to_tsvector('chinese_zh', '我喜欢吃苹果');
-- 预期输出: '我':1 '喜欢':2 '吃':3 '苹果':4

-- 测试搜索（可选）
-- SELECT * FROM chat_messages 
-- WHERE content_tsv_cn @@ websearch_to_tsquery('chinese_zh', '喜欢苹果')
-- LIMIT 5;