"""PostgreSQL 连接池管理 - Stone 数据层核心模块

统一管理数据库连接，提供：
- 异步连接池
- 会话获取（DI 和上下文管理器）
- Schema 初始化
- SQL 文件加载
"""

from contextlib import asynccontextmanager
from typing import Optional, AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy import text

from app.config import settings

# SQLAlchemy 基类（供 Models 使用）
Base = declarative_base()


class Database:
    """PostgreSQL 连接池管理器"""

    def __init__(self):
        self._engine: Optional[AsyncEngine] = None
        self._session_maker: Optional[async_sessionmaker] = None
        self._initialized: bool = False

    async def initialize(self, db_url: str = None, echo: bool = None) -> None:
        """初始化数据库连接池

        Args:
            db_url: 数据库连接 URL，默认使用 settings.DATABASE_URL
            echo: 是否打印 SQL，默认使用 settings.DEBUG
        """
        if self._initialized:
            return

        url = db_url or settings.DATABASE_URL
        echo_sql = echo if echo is not None else settings.DEBUG

        self._engine = create_async_engine(
            url,
            echo=echo_sql,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )

        self._session_maker = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        # 测试连接
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

        self._initialized = True
        print("[Stone] Database connection pool initialized")

    async def close(self) -> None:
        """关闭数据库连接池"""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_maker = None
            self._initialized = False
            print("[Stone] Database connection pool closed")

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """获取数据库会话（上下文管理器）

        用法:
            async with db.get_session() as session:
                result = await session.execute(...)
        """
        if not self._initialized or not self._session_maker:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        async with self._session_maker() as session:
            yield session

    def get_session_maker(self) -> async_sessionmaker:
        """获取会话工厂（供 DI 使用）

        用法:
            async for session in db.get_session_maker()():
                ...
        """
        if not self._initialized or not self._session_maker:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._session_maker

    async def execute_sql_file(self, sql_path: str | Path) -> None:
        """执行 SQL 文件

        Args:
            sql_path: SQL 文件路径
        """
        path = Path(sql_path)
        if not path.exists():
            raise FileNotFoundError(f"SQL file not found: {sql_path}")

        sql_content = path.read_text(encoding="utf-8")

        # PostgreSQL SQL 文件可能包含 \c 等元命令，需要处理
        # 这里只执行纯 SQL 语句
        async with self.get_session() as session:
            # 分割 SQL 语句（简单处理）
            statements = self._parse_sql_statements(sql_content)
            for stmt in statements:
                if stmt.strip():
                    await session.execute(text(stmt))
            await session.commit()

        print(f"[Stone] SQL file executed: {sql_path}")

    def _parse_sql_statements(self, content: str) -> list[str]:
        """解析 SQL 文件内容，分割成独立语句

        处理：
        - 去除注释
        - 按 ; 分割语句
        - 过滤空语句
        """
        statements = []
        current_stmt = []

        for line in content.split("\n"):
            # 去除行注释
            line = line.strip()
            if line.startswith("--"):
                continue

            # 处理 PostgreSQL 元命令（跳过）
            if line.startswith("\\"):
                continue

            current_stmt.append(line)

            # 检查语句结束
            if line.endswith(";"):
                stmt = "\n".join(current_stmt)
                statements.append(stmt)
                current_stmt = []

        # 处理最后未结束的语句
        if current_stmt:
            stmt = "\n".join(current_stmt)
            if stmt.strip():
                statements.append(stmt)

        return statements

    @property
    def engine(self) -> AsyncEngine:
        """获取底层引擎"""
        if not self._engine:
            raise RuntimeError("Database not initialized")
        return self._engine

    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized


# ============================================================
# 全局单例
# ============================================================

_db_instance: Optional[Database] = None


def get_database() -> Database:
    """获取全局数据库实例（懒加载）

    注意：首次调用需要先执行 init_database()
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance


async def init_database(db_url: str = None, echo: bool = None) -> None:
    """初始化全局数据库连接池

    Args:
        db_url: 数据库连接 URL
        echo: 是否打印 SQL
    """
    db = get_database()
    await db.initialize(db_url, echo)


async def close_database() -> None:
    """关闭全局数据库连接池"""
    db = get_database()
    await db.close()


# ============================================================
# FastAPI DI 兼容接口
# ============================================================


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入接口

    用法:
        @router.get("/")
        async def handler(session: AsyncSession = Depends(get_db_session)):
            ...
    """
    db = get_database()
    async with db.get_session() as session:
        yield session