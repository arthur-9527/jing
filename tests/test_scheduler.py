"""定时任务调度器测试脚本

测试内容：
1. 调度器启动/关闭
2. 添加任务（cron/interval）
3. 任务执行
4. 任务暂停/恢复
5. 任务删除
6. 手动触发任务
"""

import asyncio
import logging
import sys
from datetime import datetime

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# 添加项目路径
sys.path.insert(0, "/home/test/raspi_mmd/jing")

from app.scheduler import get_scheduler, JobTriggerType


# 测试任务函数
task_run_count = {}

async def test_task_hello():
    """测试任务：打印 hello"""
    print(f"[{datetime.now()}] ✅ Hello 任务执行!")
    task_run_count["hello"] = task_run_count.get("hello", 0) + 1

async def test_task_with_args(name: str, value: int):
    """测试任务：带参数"""
    print(f"[{datetime.now()}] ✅ 带参数任务执行: name={name}, value={value}")
    task_run_count["with_args"] = task_run_count.get("with_args", 0) + 1

def test_sync_task():
    """测试任务：同步函数"""
    print(f"[{datetime.now()}] ✅ 同步任务执行!")
    task_run_count["sync"] = task_run_count.get("sync", 0) + 1


async def main():
    """主测试流程"""
    print("=" * 60)
    print("定时任务调度器测试")
    print("=" * 60)
    
    scheduler = get_scheduler()
    
    # ==================== 测试 1: 启动调度器 ====================
    print("\n[测试 1] 启动调度器...")
    await scheduler.start()
    assert scheduler.is_running, "调度器应该处于运行状态"
    print("✅ 调度器启动成功")
    
    # ==================== 测试 2: 添加 Cron 任务 ====================
    print("\n[测试 2] 添加 Cron 任务...")
    scheduler.add_cron_job(
        func=test_task_hello,
        job_id="cron_hello",
        name="Cron Hello 任务",
        cron_config={"minute": "*", "second": "0"},  # 每分钟执行
        description="测试 Cron 任务",
    )
    
    job = scheduler.get_job("cron_hello")
    assert job is not None, "任务应该存在"
    print(f"✅ Cron 任务添加成功, next_run: {job.next_run_time}")
    
    # ==================== 测试 3: 添加 Interval 任务 ====================
    print("\n[测试 3] 添加 Interval 任务...")
    scheduler.add_interval_job(
        func=test_task_with_args,
        job_id="interval_args",
        name="Interval 带参数任务",
        interval_config={"seconds": 10},
        args=["测试名", 42],
        description="测试 Interval 任务",
    )
    
    job = scheduler.get_job("interval_args")
    assert job is not None, "任务应该存在"
    print(f"✅ Interval 任务添加成功, next_run: {job.next_run_time}")
    
    # ==================== 测试 4: 添加同步函数任务 ====================
    print("\n[测试 4] 添加同步函数任务...")
    scheduler.add_interval_job(
        func=test_sync_task,
        job_id="sync_interval",
        name="同步任务",
        interval_config={"seconds": 15},
    )
    
    job = scheduler.get_job("sync_interval")
    assert job is not None, "任务应该存在"
    print(f"✅ 同步任务添加成功, next_run: {job.next_run_time}")
    
    # ==================== 测试 5: 获取所有任务 ====================
    print("\n[测试 5] 获取所有任务...")
    jobs = scheduler.get_all_jobs_info()
    print(f"当前任务数: {len(jobs)}")
    for job_info in jobs:
        print(f"  - {job_info.job_id}: {job_info.name}, trigger={job_info.trigger_type}, next_run={job_info.next_run_time}")
    assert len(jobs) == 3, "应该有 3 个任务"
    print("✅ 任务列表获取成功")
    
    # ==================== 测试 6: 暂停任务 ====================
    print("\n[测试 6] 暂停任务...")
    result = scheduler.pause_job("sync_interval")
    assert result, "暂停应该成功"
    
    job_info = scheduler.get_job_info("sync_interval")
    assert not job_info.enabled, "任务应该处于禁用状态"
    print("✅ 任务暂停成功")
    
    # ==================== 测试 7: 恢复任务 ====================
    print("\n[测试 7] 恢复任务...")
    result = scheduler.resume_job("sync_interval")
    assert result, "恢复应该成功"
    
    job_info = scheduler.get_job_info("sync_interval")
    assert job_info.enabled, "任务应该处于启用状态"
    print("✅ 任务恢复成功")
    
    # ==================== 测试 8: 手动触发任务 ====================
    print("\n[测试 8] 手动触发任务...")
    result = await scheduler.run_job_now("cron_hello")
    assert result, "手动触发应该成功"
    assert task_run_count.get("hello", 0) >= 1, "任务应该已执行"
    print("✅ 手动触发成功")
    
    # ==================== 测试 9: 修改任务 ====================
    print("\n[测试 9] 修改任务...")
    result = scheduler.modify_job(
        job_id="interval_args",
        name="修改后的名称",
        description="修改后的描述",
    )
    assert result, "修改应该成功"
    
    job_info = scheduler.get_job_info("interval_args")
    assert job_info.name == "修改后的名称", "名称应该已修改"
    print("✅ 任务修改成功")
    
    # ==================== 测试 10: 删除任务 ====================
    print("\n[测试 10] 删除任务...")
    result = scheduler.remove_job("sync_interval")
    assert result, "删除应该成功"
    
    job = scheduler.get_job("sync_interval")
    assert job is None, "任务应该不存在"
    
    jobs = scheduler.get_all_jobs_info()
    assert len(jobs) == 2, "应该剩下 2 个任务"
    print("✅ 任务删除成功")
    
    # ==================== 测试 11: 等待任务执行 ====================
    print("\n[测试 11] 等待任务自动执行 (10秒)...")
    await asyncio.sleep(10)
    print(f"任务执行计数: hello={task_run_count.get('hello', 0)}, with_args={task_run_count.get('with_args', 0)}")
    print("✅ 任务自动执行测试完成")
    
    # ==================== 测试 12: 关闭调度器 ====================
    print("\n[测试 12] 关闭调度器...")
    await scheduler.stop(wait=True)
    assert not scheduler.is_running, "调度器应该已关闭"
    print("✅ 调度器关闭成功")
    
    # ==================== 总结 ====================
    print("\n" + "=" * 60)
    print("所有测试通过!")
    print("=" * 60)
    print(f"任务执行统计:")
    print(f"  - hello 任务: {task_run_count.get('hello', 0)} 次")
    print(f"  - with_args 任务: {task_run_count.get('with_args', 0)} 次")
    print(f"  - sync 任务: {task_run_count.get('sync', 0)} 次")


if __name__ == "__main__":
    asyncio.run(main())