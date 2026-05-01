-- ============================================================
-- PostgreSQL 扩展初始化
-- 执行方式：sudo -u postgres psql -d agent_backend -f 00_extensions.sql
-- ============================================================

-- 1. 向量扩展 (pgvector)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. UUID 生成扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 3. 中文分词扩展 (zhparser)
-- ⚠️ 需要先安装 zhparser：apt install postgresql-<version>-zhparser
CREATE EXTENSION IF NOT EXISTS zhparser;

-- 4. 创建中文分词配置
DROP TEXT SEARCH CONFIGURATION IF EXISTS chinese_zh CASCADE;
CREATE TEXT SEARCH CONFIGURATION chinese_zh (PARSER = zhparser);
ALTER TEXT SEARCH CONFIGURATION chinese_zh ADD MAPPING FOR n,v,a,i,e,l WITH simple;

\echo '扩展初始化完成: vector, uuid-ossp, zhparser'