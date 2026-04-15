#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清空动作相关数据脚本

清空以下表：
- motion_tag_map (动作-标签关联)
- keyframes (关键帧数据)
- motions (动作元数据)
- motion_tags (标签字典)

执行: python scripts/clear_motion_data.py
"""

import asyncio
import asyncpg
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))


async def get_db_connection():
    """从 .env 文件读取数据库连接信息"""
    from app.config import settings
    # 解析 asyncpg URL
    db_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "")
    parts = db_url.split("@")
    user_pass = parts[0].split(":")
    host_db = parts[1].split("/")
    host_port = host_db[0].split(":")
    
    return await asyncpg.connect(
        user=user_pass[0],
        password=user_pass[1],
        host=host_port[0],
        port=int(host_port[1]) if len(host_port) > 1 else 5432,
        database=host_db[1]
    )


async def clear_motion_data():
    """清空动作相关表"""
    
    print("=" * 60)
    print("清空动作数据库脚本")
    print("=" * 60)
    
    conn = None
    try:
        conn = await get_db_connection()
        
        # 先查询当前数据量
        print("\n[1] 查询当前数据量...")
        
        tables = ['motions', 'keyframes', 'motion_tags', 'motion_tag_map']
        for table in tables:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            print(f"    {table}: {count} 条记录")
        
        # 开始清空
        print("\n[2] 清空数据表...")
        
        # 清空顺序很重要：先清关联，再清子表，最后清主表
        # motion_tag_map -> keyframes -> motions -> motion_tags
        
        await conn.execute("TRUNCATE TABLE motion_tag_map CASCADE")
        print("    ✓ motion_tag_map 已清空")
        
        await conn.execute("TRUNCATE TABLE keyframes CASCADE")
        print("    ✓ keyframes 已清空")
        
        await conn.execute("TRUNCATE TABLE motions CASCADE")
        print("    ✓ motions 已清空")
        
        await conn.execute("TRUNCATE TABLE motion_tags CASCADE")
        print("    ✓ motion_tags 已清空")
        
        # 验证结果
        print("\n[3] 验证清空结果...")
        for table in tables:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            status = "✓" if count == 0 else "✗"
            print(f"    {status} {table}: {count} 条记录")
        
        print("\n" + "=" * 60)
        print("✓ 动作数据已全部清空！")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n✗ 错误: {e}")
        return False
        
    finally:
        if conn:
            await conn.close()


if __name__ == "__main__":
    success = asyncio.run(clear_motion_data())
    sys.exit(0 if success else 1)
