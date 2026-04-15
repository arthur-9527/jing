#!/usr/bin/env python3
"""
OpenClaw服务基础验证测试

验证：
1. 配置加载
2. 模块导入
3. 身份文件读取
4. Redis连接
"""

import asyncio
import sys
import os
from pathlib import Path

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_imports():
    """测试模块导入"""
    print("=" * 60)
    print("测试1: 模块导入")
    print("=" * 60)

    try:
        from app.services.openclaw import (
            get_openclaw_config,
            OpenClawTaskManager,
            get_openclaw_manager,
        )
        print("✓ 核心模块导入成功")

        from app.services.openclaw.models import (
            Task, TaskStatus, SessionState, SessionStatus,
        )
        print("✓ 数据模型导入成功")

        from app.services.openclaw.redis_repo import OpenClawTaskRepository
        print("✓ Redis仓库导入成功")

        from app.services.openclaw.ws_client import OpenClawWSClient
        print("✓ WebSocket客户端导入成功")

        return True
    except Exception as e:
        print(f"✗ 模块导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_config():
    """测试配置加载"""
    print("\n" + "=" * 60)
    print("测试2: 配置加载")
    print("=" * 60)

    try:
        from app.services.openclaw import get_openclaw_config

        config = get_openclaw_config()
        print(f"✓ 配置加载成功")
        print(f"  - WebSocket URL: {config.ws.ws_url}")
        print(f"  - Session Keys: {config.session.session_keys}")
        print(f"  - Redis URL: {config.redis.redis_url}")
        print(f"  - 身份文件: {config.ws.identity_file}")

        return True
    except Exception as e:
        print(f"✗ 配置加载失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_identity_file():
    """测试身份文件"""
    print("\n" + "=" * 60)
    print("测试3: 身份文件")
    print("=" * 60)

    try:
        from pathlib import Path

        identity_file = Path.home() / ".openclaw" / "pipecat_identity.json"
        print(f"✓ 身份文件路径: {identity_file}")

        if not identity_file.exists():
            print(f"✗ 身份文件不存在")
            return False

        print(f"✓ 身份文件存在")

        # 读取内容
        import json
        with open(identity_file) as f:
            data = json.load(f)

        print(f"✓ 身份文件读取成功")
        print(f"  - DeviceID: {data.get('deviceId', 'N/A')[:16]}...")
        print(f"  - Version: {data.get('version', 'N/A')}")

        return True
    except Exception as e:
        print(f"✗ 身份文件测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_redis_connection():
    """测试Redis连接"""
    print("\n" + "=" * 60)
    print("测试4: Redis连接")
    print("=" * 60)

    try:
        from app.services.openclaw.redis_repo import OpenClawTaskRepository

        repo = OpenClawTaskRepository()
        await repo.connect()
        print("✓ Redis连接成功")

        # 测试ping
        import redis.asyncio as aioredis
        redis_client = await aioredis.from_url("redis://localhost:6379/1")
        await redis_client.ping()
        print("✓ Redis ping成功")

        await repo.disconnect()
        print("✓ Redis断开成功")

        return True
    except Exception as e:
        print(f"✗ Redis连接失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_data_models():
    """测试数据模型"""
    print("\n" + "=" * 60)
    print("测试5: 数据模型")
    print("=" * 60)

    try:
        from app.services.openclaw.models import Task, TaskStatus, SessionState, SessionStatus

        # 创建Task
        task = Task(
            id="test-001",
            tool_prompt="测试任务",
            status=TaskStatus.PENDING,
        )
        print("✓ Task创建成功")

        # 序列化
        task_dict = task.to_dict()
        print("✓ Task序列化成功")

        # 反序列化
        task_restored = Task.from_dict(task_dict)
        print("✓ Task反序列化成功")

        # 创建SessionState
        session = SessionState(session_key="agent:main:chat1")
        print("✓ SessionState创建成功")

        # 分配任务
        session.assign_task("task-001")
        assert session.is_busy(), "Session应该是BUSY状态"
        print("✓ 任务分配成功")

        # 释放任务
        session.release()
        assert session.is_idle(), "Session应该是IDLE状态"
        print("✓ 任务释放成功")

        return True
    except Exception as e:
        print(f"✗ 数据模型测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """运行所有基础验证测试"""
    print("\n" + "=" * 60)
    print("OpenClaw服务基础验证测试")
    print("=" * 60)
    print()

    results = []

    # 运行测试
    results.append(await test_imports())
    results.append(await test_config())
    results.append(await test_identity_file())
    results.append(await test_redis_connection())
    results.append(await test_data_models())

    # 总结
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 60)

    if passed == total:
        print("\n✅ 所有基础验证测试通过！")
        print("\n下一步：运行完整功能测试")
        print("  python tests/test_openclaw_full.py")
    else:
        print("\n❌ 部分测试失败，请检查错误信息")

    print()


if __name__ == "__main__":
    asyncio.run(main())