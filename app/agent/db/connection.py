"""Agent DB 初始化入口（统一到 SQLAlchemy engine/pool）。

历史上该模块维护了独立的 asyncpg 连接池；现在改为：
- 仅保留 init_db() 执行 init_schema.sql（创建 agent 表/索引/extension）
- close_pool() 保持兼容但为 no-op（engine 的生命周期由 app/database.py 管理）

注意：
- init_schema.sql 是多条语句，SQLAlchemy 连接通常需要逐条执行。
"""

from __future__ import annotations

import os

from sqlalchemy import text

from app.database import engine


async def close_pool():
    """兼容旧调用：SQLAlchemy engine 在 app/database.close_db() 统一 dispose。"""


def _split_sql_statements(sql: str) -> list[str]:
    # 够用的简单分句：按 ; 切分并去掉空语句与纯注释行。
    # 本项目 init_schema.sql 结构简单（无函数体/$$ 块），不需要更重的 SQL parser。
    statements: list[str] = []
    for part in sql.split(";"):
        stmt = part.strip()
        if not stmt:
            continue
        # 去掉全行注释
        lines = []
        for line in stmt.splitlines():
            striped = line.strip()
            if not striped or striped.startswith("--"):
                continue
            lines.append(line)
        cleaned = "\n".join(lines).strip()
        if cleaned:
            statements.append(cleaned)
    return statements


async def init_db():
    """初始化 Agent 侧 schema（幂等）。"""
    schema_path = os.path.join(os.path.dirname(__file__), "init_schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()

    statements = _split_sql_statements(sql)
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))
