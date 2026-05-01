"""API 路由"""

from app.routers.motions import router as motions_router
from app.routers.tags import router as tags_router
from app.realtime.router import router as rt_router

__all__ = ["motions_router", "tags_router", "rt_router"]
