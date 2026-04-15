"""标签相关 Schema"""

from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID


class TagItem(BaseModel):
    """标签项"""
    id: UUID
    tag_type: str
    tag_name: str
    display_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class TagResponse(BaseModel):
    """标签列表响应"""
    tags: List[TagItem]