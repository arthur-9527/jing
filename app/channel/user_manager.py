"""用户管理器

负责：
- 平台账号 → 统一 user_id 映射
- 用户创建/查询
- 平台绑定管理
"""

import logging
from typing import Optional

from sqlalchemy import Column, String, TIMESTAMP, Boolean, func, select, Table, MetaData
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.stone import get_database

# 创建 metadata（用于定义 Table）
_metadata = MetaData()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Table definitions (SQLAlchemy Core)
# ---------------------------------------------------------------------------

# 统一用户表
im_users = Table(
    "im_users",
    _metadata,
    Column("user_id", String(64), primary_key=True),     # "u_001"
    Column("display_name", String(128), nullable=True),
    Column("user_group_id", String(64), nullable=True),  # 预留：未来记忆共享
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

# 平台账号绑定表
im_platform_bindings = Table(
    "im_platform_bindings",
    _metadata,
    Column("id", String(64), primary_key=True),  # "{platform}:{platform_user_id}"
    Column("platform", String(32), nullable=False),     # "wechat", "telegram"
    Column("platform_user_id", String(128), nullable=False),  # 平台内ID
    Column("user_id", String(64), nullable=False),      # 映射到统一用户
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
    # 注意：需要在数据库层面添加唯一约束
    # UNIQUE(platform, platform_user_id)
)


# 导出 Table 对象供迁移脚本使用
__all__ = ["im_users", "im_platform_bindings", "UserManager"]


# ---------------------------------------------------------------------------
# UserManager
# ---------------------------------------------------------------------------

class UserManager:
    """用户管理器
    
    负责：
    - 平台账号 → 统一 user_id 映射
    - 用户创建/查询
    - 平台绑定管理
    """
    
    def __init__(self):
        self._user_counter = 0  # 用于生成新用户 ID
    
    async def get_or_create_user(
        self,
        platform: str,
        platform_user_id: str,
    ) -> str:
        """获取或创建用户
        
        1. 查询 PlatformBinding 是否存在
        2. 存在 → 返回 user_id
        3. 不存在 → 创建新 User + PlatformBinding
        
        Args:
            platform: 平台标识 (wechat/telegram)
            platform_user_id: 平台内用户 ID
        
        Returns:
            统一用户 ID
        """
        binding_id = f"{platform}:{platform_user_id}"
        
        async with get_database().get_session() as session:
            async with session.begin():
                # 查询绑定
                stmt = select(im_platform_bindings.c.user_id).where(
                    im_platform_bindings.c.id == binding_id
                )
                result = await session.scalar(stmt)
                
                if result:
                    logger.debug(f"[UserManager] 已存在绑定: {binding_id} → {result}")
                    return result
                
                # 创建新用户
                user_id = await self._generate_user_id()
                
                # 插入用户
                stmt = pg_insert(im_users).values(
                    user_id=user_id,
                    display_name=None,
                    user_group_id=None,
                )
                await session.execute(stmt)
                
                # 插入绑定
                stmt = pg_insert(im_platform_bindings).values(
                    id=binding_id,
                    platform=platform,
                    platform_user_id=platform_user_id,
                    user_id=user_id,
                )
                await session.execute(stmt)
                
                logger.info(f"[UserManager] 新用户创建: {binding_id} → {user_id}")
                
                return user_id
    
    async def get_platform_user_id(
        self,
        user_id: str,
        platform: str,
    ) -> Optional[str]:
        """反向查询：统一 user_id → 平台账号 ID
        
        Args:
            user_id: 统一用户 ID
            platform: 平台标识
        
        Returns:
            平台用户 ID，如果不存在则返回 None
        """
        async with get_database().get_session() as session:
            stmt = select(im_platform_bindings.c.platform_user_id).where(
                im_platform_bindings.c.user_id == user_id,
                im_platform_bindings.c.platform == platform,
            )
            result = await session.scalar(stmt)
            return result
    
    async def get_user_bindings(self, user_id: str) -> list[dict]:
        """获取用户的所有平台绑定
        
        Args:
            user_id: 统一用户 ID
        
        Returns:
            绑定列表 [{"platform": "wechat", "platform_user_id": "xxx"}, ...]
        """
        async with get_database().get_session() as session:
            stmt = select(
                im_platform_bindings.c.platform,
                im_platform_bindings.c.platform_user_id,
            ).where(
                im_platform_bindings.c.user_id == user_id,
            )
            rows = (await session.execute(stmt)).mappings().all()
            return [dict(r) for r in rows]
    
    async def _generate_user_id(self) -> str:
        """生成新用户 ID
        
        格式: u_001, u_002, ...
        
        注意：这里使用简单的计数器，实际生产环境应使用数据库序列。
        """
        async with get_database().get_session() as session:
            # 获取当前最大用户数
            stmt = select(func.count(im_users.c.user_id))
            count = await session.scalar(stmt) or 0
            
            user_id = f"u_{count + 1:04d}"
            return user_id


# ---------------------------------------------------------------------------
# 全局实例
# ---------------------------------------------------------------------------

_user_manager: Optional[UserManager] = None


def get_user_manager() -> UserManager:
    """获取用户管理器实例"""
    global _user_manager
    if _user_manager is None:
        _user_manager = UserManager()
    return _user_manager
