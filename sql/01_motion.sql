-- ============================================================
-- Motion 相关表（动作元数据 + 关键帧 + 标签）
-- 执行方式：sudo -u postgres psql -d agent_backend -f 01_motion.sql
-- ============================================================

-- ============================================================
-- 1. motions 表 (动作元数据)
-- ============================================================
CREATE TABLE motions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(255),
    description TEXT,
    original_fps INTEGER DEFAULT 30,
    original_frames INTEGER NOT NULL,
    original_duration FLOAT NOT NULL,
    keyframe_count INTEGER NOT NULL,
    is_loopable BOOLEAN DEFAULT FALSE,
    is_interruptible BOOLEAN DEFAULT TRUE,
    status VARCHAR(20) DEFAULT 'active',
    embedding VECTOR(1536),
    source_file VARCHAR(512),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_motions_name ON motions(name);
CREATE INDEX idx_motions_duration ON motions(original_duration);
CREATE INDEX idx_motions_status ON motions(status);
CREATE INDEX idx_motions_embedding ON motions USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ============================================================
-- 2. keyframes 表 (关键帧数据)
-- ============================================================
CREATE TABLE keyframes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motion_id UUID NOT NULL REFERENCES motions(id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    original_frame INTEGER NOT NULL,
    timestamp FLOAT NOT NULL,
    bone_data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(motion_id, frame_index)
);

CREATE INDEX idx_keyframes_motion ON keyframes(motion_id, frame_index);
CREATE INDEX idx_keyframes_timestamp ON keyframes(motion_id, timestamp);

-- ============================================================
-- 3. motion_tags 表 (标签字典)
-- ============================================================
CREATE TABLE motion_tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tag_type VARCHAR(50) NOT NULL,
    tag_name VARCHAR(100) NOT NULL,
    display_name VARCHAR(255),
    description TEXT,
    embedding VECTOR(1536),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tag_type, tag_name)
);

CREATE INDEX idx_motion_tags_type ON motion_tags(tag_type);
CREATE INDEX idx_motion_tags_name ON motion_tags(tag_name);

-- ============================================================
-- 4. motion_tag_map 表 (动作-标签关联)
-- ============================================================
CREATE TABLE motion_tag_map (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motion_id UUID NOT NULL REFERENCES motions(id) ON DELETE CASCADE,
    tag_id UUID NOT NULL REFERENCES motion_tags(id) ON DELETE CASCADE,
    weight FLOAT DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(motion_id, tag_id)
);

CREATE INDEX idx_motion_tag_map_motion ON motion_tag_map(motion_id);
CREATE INDEX idx_motion_tag_map_tag ON motion_tag_map(tag_id);

\echo 'Motion 表创建完成: motions, keyframes, motion_tags, motion_tag_map'