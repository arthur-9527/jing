"""数据库连接配置"""

from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import text
from app.config import settings

# 创建异步数据库引擎
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# 创建会话工厂
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# 声明基类
Base = declarative_base()


async def get_db() -> AsyncSession:
    """获取数据库会话的依赖注入函数"""
    async with async_session_maker() as session:
        yield session


@asynccontextmanager
async def get_db_session() -> AsyncSession:
    """非 DI 场景使用的上下文管理器，确保 session 正确关闭"""
    async with async_session_maker() as session:
        yield session


async def init_db():
    """初始化数据库连接"""
    # 测试连接
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        print("Database connection successful")


async def close_db():
    """关闭数据库连接"""
    await engine.dispose()