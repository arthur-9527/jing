"""标签相关 API 路由"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.stone import get_db_session
from app.services.search_service import SearchService
from app.schemas.tag import TagResponse, TagItem

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("", response_model=TagResponse)
async def get_tags(
    tag_type: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
):
    """获取标签列表"""
    search_service = SearchService(db)
    tags = await search_service.get_all_tags(tag_type)
    
    return TagResponse(
        tags=[
            TagItem(
                id=t["id"],
                tag_type=t["tag_type"],
                tag_name=t["tag_name"],
                display_name=t["display_name"],
            )
            for t in tags
        ]
    )