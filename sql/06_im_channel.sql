-- ============================================================
-- IM Channel 相关表（用户和平台绑定）
-- 执行方式：sudo -u postgres psql -d agent_backend -f 06_im_channel.sql
-- ============================================================

-- ============================================================
-- 1. im_users 表 (统一用户表)
-- ============================================================
CREATE TABLE IF NOT EXISTS im_users (
    user_id       VARCHAR(64) PRIMARY KEY,     -- "u_001"
    display_name  VARCHAR(128),
    user_group_id VARCHAR(64),                 -- 预留：未来记忆共享
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================
-- 2. im_platform_bindings 表 (平台账号绑定表)
-- ============================================================
CREATE TABLE IF NOT EXISTS im_platform_bindings (
    id               VARCHAR(64) PRIMARY KEY,  -- "{platform}:{platform_user_id}"
    platform         VARCHAR(32) NOT NULL,     -- "wechat", "telegram"
    platform_user_id VARCHAR(128) NOT NULL,    -- 平台内ID
    user_id          VARCHAR(64) NOT NULL REFERENCES im_users(user_id),
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(platform, platform_user_id)         -- 一个平台账号只绑一个user
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_bindings_user ON im_platform_bindings(user_id);
CREATE INDEX IF NOT EXISTS idx_bindings_platform ON im_platform_bindings(platform, platform_user_id);

\echo 'IM Channel 表创建完成: im_users, im_platform_bindings'