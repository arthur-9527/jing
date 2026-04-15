"""API 路由"""

from app.routers.motions import router as motions_router
from app.routers.tags import router as tags_router
from app.routers.ws import router as ws_router
from app.routers.agent import router as agent_router

__all__ = ["motions_router", "tags_router", "ws_router", "agent_router"]