-- ============================================================
-- MMD Agent Backend - 统一数据库初始化脚本
-- 执行方式：sudo -u postgres psql -f init_all.sql
-- ============================================================

\echo '========================================'
\echo 'MMD Agent Backend 数据库初始化'
\echo '========================================'

-- 1. 创建数据库（如果不存在）
-- 注意：需要在 postgres 数据库中执行此部分
-- SELECT 'CREATE DATABASE agent_backend' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_backend')\gexec

-- 如果数据库已存在，直接连接
\c agent_backend

\echo ''
\echo 'Step 1: 初始化扩展...'
\i 00_extensions.sql

\echo ''
\echo 'Step 2: 创建 Motion 表...'
\i 01_motion.sql

\echo ''
\echo 'Step 3: 创建 Agent 表...'
\i 02_agent.sql

\echo ''
\echo 'Step 4: 创建 Memory 表...'
\i 03_memory.sql

\echo ''
\echo 'Step 5: 创建 Affection 表...'
\i 04_affection.sql

\echo ''
\echo 'Step 6: 创建 Daily Life 表...'
\i 05_daily_life.sql

\echo ''
\echo 'Step 7: 创建 IM Channel 表...'
\i 06_im_channel.sql

\echo ''
\echo 'Step 8: 添加中文分词 FTS 列...'
\i 07_fts_columns.sql

\echo ''
\echo '========================================'
\echo '数据库初始化完成!'
\echo '========================================'
\echo ''
\echo '数据库：agent_backend'
\echo '包含表：'
\echo '  - Motion (4): motions, keyframes, motion_tags, motion_tag_map'
\echo '  - Agent (4): character_background, character_emotion, user_history, agent_state'
\echo '  - Memory (7): chat_messages, key_events, heartbeat_events, daily_diary, weekly_index, monthly_index, annual_index'
\echo '  - Affection (1): affection_state'
\echo '  - Daily Life (1): daily_life_events'
\echo '  - IM Channel (2): im_users, im_platform_bindings'
\echo '  总计：19 张表'
\echo ''
\echo '⚠️ 重要提示：'
\echo '  1. Docker 部署：密码由 POSTGRES_PASSWORD 环境变量控制'
\echo '  2. 手动部署：请执行 ALTER USER admin WITH PASSWORD ''你的密码'';'