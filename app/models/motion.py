"""动作和关键帧模型"""

from sqlalchemy import Column, String, Integer, Float, Boolean, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from app.database import Base
from app.config import settings
import uuid


class Motion(Base):
    """动作元数据表"""
    
    __tablename__ = "motions"
    
    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # 基本信息
    name = Column(String(255), nullable=False, unique=True, index=True)
    display_name = Column(String(255))
    description = Column(Text)
    
    # 时长信息
    original_fps = Column(Integer, default=30)
    original_frames = Column(Integer, nullable=False)
    original_duration = Column(Float, nullable=False)
    
    # 抽帧信息
    keyframe_count = Column(Integer, nullable=False)
    
    # 动作属性
    is_loopable = Column(Boolean, default=False)
    is_interruptible = Column(Boolean, default=True)
    
    # 状态
    status = Column(String(20), default='active', index=True)
    
    # 语义搜索向量（使用配置的维度）
    embedding = Column(Vector(settings.EMBEDDING_DIM))
    
    # 元数据
    source_file = Column(String(512))
    
    # 关系
    keyframes = relationship("Keyframe", back_populates="motion", cascade="all, delete-orphan")
    tag_map = relationship("MotionTagMap", back_populates="motion", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Motion(id={self.id}, name='{self.name}')>"


class Keyframe(Base):
    """关键帧表"""
    
    __tablename__ = "keyframes"
    
    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # 外键
    motion_id = Column(UUID(as_uuid=True), ForeignKey("motions.id", ondelete="CASCADE"), nullable=False)
    
    # 帧索引
    frame_index = Column(Integer, nullable=False)
    original_frame = Column(Integer, nullable=False)
    
    # 时间戳
    timestamp = Column(Float, nullable=False)
    
    # 骨骼数据 (JSONB)
    bone_data = Column(JSONB, nullable=False)
    
    # 约束
    __table_args__ = (
        UniqueConstraint('motion_id', 'frame_index', name='uq_motion_frame_index'),
    )
    
    # 关系
    motion = relationship("Motion", back_populates="keyframes")
    
    def __repr__(self):
        return f"<Keyframe(motion_id={self.motion_id}, frame_index={self.frame_index})>"