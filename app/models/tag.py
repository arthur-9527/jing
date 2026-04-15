"""标签相关模型"""

from sqlalchemy import Column, String, Float, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from app.database import Base
from app.config import settings
import uuid


class MotionTag(Base):
    """标签字典表"""
    
    __tablename__ = "motion_tags"
    
    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # 标签信息
    tag_type = Column(String(50), nullable=False, index=True)
    tag_name = Column(String(100), nullable=False, index=True)
    display_name = Column(String(255))
    description = Column(Text)
    
    # 语义向量（使用配置的维度）
    embedding = Column(Vector(settings.EMBEDDING_DIM))
    
    # 约束
    __table_args__ = (
        UniqueConstraint('tag_type', 'tag_name', name='uq_tag_type_name'),
    )
    
    # 关系
    tag_map = relationship("MotionTagMap", back_populates="tag", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<MotionTag(type='{self.tag_type}', name='{self.tag_name}')>"


class MotionTagMap(Base):
    """动作 - 标签关联表"""
    
    __tablename__ = "motion_tag_map"
    
    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # 外键
    motion_id = Column(UUID(as_uuid=True), ForeignKey("motions.id", ondelete="CASCADE"), nullable=False, index=True)
    tag_id = Column(UUID(as_uuid=True), ForeignKey("motion_tags.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # 权重
    weight = Column(Float, default=1.0)
    
    # 约束
    __table_args__ = (
        UniqueConstraint('motion_id', 'tag_id', name='uq_motion_tag'),
    )
    
    # 关系
    motion = relationship("Motion", back_populates="tag_map")
    tag = relationship("MotionTag", back_populates="tag_map")
    
    def __repr__(self):
        return f"<MotionTagMap(motion_id={self.motion_id}, tag_id={self.tag_id})>"