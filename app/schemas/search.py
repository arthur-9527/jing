"""搜索相关 Schema"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from uuid import UUID


class TagSearchQuery(BaseModel):
    """标签搜索查询参数"""
    emotion: Optional[str] = None
    action: Optional[str] = None
    scene: Optional[str] = None
    intensity: Optional[str] = None
    style: Optional[str] = None
    speed: Optional[str] = None
    rhythm: Optional[str] = None
    complexity: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)


class TagSearchResult(BaseModel):
    """标签搜索结果项"""
    id: UUID
    name: str
    display_name: Optional[str] = None
    original_duration: float
    match_score: float
    matched_tags: List[str]


class TagSearchResponse(BaseModel):
    """标签搜索响应"""
    query: Dict[str, Any]
    results: List[TagSearchResult]


class SemanticSearchQuery(BaseModel):
    """语义搜索查询参数"""
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=100)


class SemanticSearchResult(BaseModel):
    """语义搜索结果项"""
    id: UUID
    name: str
    display_name: Optional[str] = None
    original_duration: float
    similarity: float


class SemanticSearchResponse(BaseModel):
    """语义搜索响应"""
    query: str
    results: List[SemanticSearchResult]