"""动作相关 Schema"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from uuid import UUID


class MotionListItem(BaseModel):
    """动作列表项"""
    id: UUID
    name: str
    display_name: Optional[str] = None
    original_duration: float
    keyframe_count: int
    is_loopable: bool
    is_interruptible: bool
    status: str
    
    class Config:
        from_attributes = True


class MotionDetail(BaseModel):
    """动作详情"""
    id: UUID
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    original_fps: int
    original_frames: int
    original_duration: float
    keyframe_count: int
    is_loopable: bool
    is_interruptible: bool
    status: str
    source_file: Optional[str] = None
    tags: Optional[List[Dict[str, Any]]] = None
    
    class Config:
        from_attributes = True


class KeyframeItem(BaseModel):
    """关键帧数据"""
    frame_index: int
    original_frame: int
    timestamp: float
    bone_data: Dict[str, Any]
    
    class Config:
        from_attributes = True


class MotionListQuery(BaseModel):
    """动作列表查询参数"""
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    status: Optional[str] = None
    is_loopable: Optional[bool] = None
    min_duration: Optional[float] = None
    max_duration: Optional[float] = None


class MotionListResponse(BaseModel):
    """动作列表响应"""
    total: int
    page: int
    page_size: int
    items: List[MotionListItem]


class MotionDetailResponse(BaseModel):
    """动作详情响应"""
    data: MotionDetail


class KeyframeResponse(BaseModel):
    """关键帧列表响应"""
    motion_id: UUID
    motion_name: str
    total_frames: int
    fps: int
    keyframes: List[KeyframeItem]


class SingleKeyframeResponse(BaseModel):
    """单帧响应"""
    motion_id: UUID
    frame_index: int
    original_frame: int
    timestamp: float
    bone_data: Dict[str, Any]