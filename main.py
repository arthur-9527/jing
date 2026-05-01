"""MMD Agent Backend - 主应用入口"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from app.config import settings
from app.errors import AppException
from app.stone import init_database, close_database, init_redis_pool, close_redis_pool
from app.routers import motions_router, tags_router, rt_router
from app.routers.vmd_upload import router as vmd_upload_router
from app.realtime import get_realtime_manager, reset_realtime_manager
from app.realtime.agent_service import stop_agent_service
from app.scheduler import get_scheduler
from app.channel.manager import ChannelManager, get_channel_manager, reset_channel_manager
from app.channel.processor import init_channel_processor, reset_channel_processor

# ===== 日志配置 =====
log_level = settings.LOG_LEVEL.upper()

# 标准 logging
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
# 抑制 asyncpg 的大量 SQL 日志（SQLAlchemy async driver 底层仍可能使用 asyncpg）
logging.getLogger("asyncpg").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("hpack").setLevel(logging.WARNING)
logging.getLogger("h2").setLevel(logging.WARNING)
# 抑制 OpenAI SDK 的详细请求日志
logging.getLogger("openai._base_client").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
# loguru 重新配置
logger.remove()
logger.add(sys.stderr, level=log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    print("Starting up...", flush=True)

    # 初始化 Stone 数据层（统一 PostgreSQL + Redis）
    try:
        await init_database()
        print("Stone database initialized")
    except Exception as e:
        print(f"WARNING: Stone database init failed: {e}")

    # 初始化 Redis 连接池
    try:
        await init_redis_pool()
        print("Redis pool initialized")
    except Exception as e:
        print(f"WARNING: Redis pool init failed: {e}")

    # 预加载 Embedding 模型（启动时加载到内存）
    print("Loading Embedding model...")
    import time
    from app.agent.memory.embedding import get_embedding
    try:
        # 预热调用 - 执行一次实际推理并打印时间
        t0 = time.time()
        test_embedding = await get_embedding("系统初始化测试")
        elapsed = (time.time() - t0) * 1000
        print(f"Embedding warmup: {elapsed:.1f}ms, dim={len(test_embedding)}")
    except Exception as e:
        print(f"WARNING: Embedding model load failed: {e}")

    # 启动定时任务调度器（实时流 + IM 共用）
    if settings.SCHEDULER_ENABLED:
        print("Starting Scheduler...")
        try:
            scheduler = get_scheduler()
            await scheduler.start()
            print(f"Scheduler started, jobs: {len(scheduler.get_jobs())}")
        except Exception as e:
            print(f"WARNING: Scheduler start failed: {e}")

    # 启动实时语音流（Pipecat Pipeline + 情绪/好感度/Agent 服务）
    print("Starting Realtime voice stream...")
    try:
        realtime_manager = get_realtime_manager()
        await realtime_manager.start()
        print("Realtime voice stream started")
    except Exception as e:
        print(f"WARNING: Realtime voice stream start failed: {e}")
        import traceback
        traceback.print_exc()

    # 启动 Channel 层（平行于实时语音流 Pipeline）
    print("Starting Channel layer...")
    try:
        # 获取 ChannelManager
        channel_manager = get_channel_manager()
        
        # 注册 QQChannel
        from app.channel.providers.qq.channel import QQChannel
        qq_channel = QQChannel()
        channel_manager.register_channel(qq_channel)
        print(f"QQ Channel registered: {qq_channel.channel_id}")
        
        # 加载角色配置并初始化 ChannelProcessor（IM LLM 模式）
        from app.agent.character.loader import load_character
        
        # 获取 QQ Channel 绑定的角色 ID（从第一个 Bot 配置获取）
        character_id = qq_channel.get_bot_character("default") or "daji"
        print(f"Loading character config: {character_id}")
        
        # 加载角色配置
        character_config_path = f"config/characters/{character_id}"
        character_config = load_character(character_config_path)
        
        # 转换为字典格式
        character_config_dict = character_config.model_dump()
        character_config_dict["character_id"] = character_id
        
        # 查找参考图路径
        reference_image_path = None
        for ext in ["png", "jpg", "jpeg"]:
            candidate = os.path.join(character_config_path, f"reference.{ext}")
            if os.path.exists(candidate):
                reference_image_path = candidate
                print(f"Found reference image: {reference_image_path}")
                break
        
        # 初始化 ChannelProcessor（IM LLM 模式，而非 Echo 模式）
        processor = init_channel_processor(
            character_config=character_config_dict,
            reference_image_path=reference_image_path,
        )
        channel_manager.set_response_handler(processor.handle)
        print(f"ChannelProcessor initialized (IM LLM mode), character: {character_id}")
        
        # 启动 ChannelManager
        await channel_manager.start()
        print("Channel layer started")
    except Exception as e:
        print(f"WARNING: Channel layer start failed: {e}")
        import traceback
        traceback.print_exc()

    # 启动日常事务调度器
    print("Starting Daily Life Scheduler...")
    try:
        from app.daily_life import get_daily_life_scheduler
        daily_life_scheduler = get_daily_life_scheduler(
            character_id=character_id,
            reference_image_path=reference_image_path,
        )
        await daily_life_scheduler.start()
        print(f"Daily Life Scheduler started, status: {daily_life_scheduler.get_status()}")
    except Exception as e:
        print(f"WARNING: Daily Life Scheduler start failed: {e}")
        import traceback
        traceback.print_exc()

    yield

    # 关闭时清理（带超时机制）
    print("Shutting down...")
    
    shutdown_timeout = settings.GRACEFUL_SHUTDOWN_TIMEOUT
    import asyncio

    async def _shutdown_with_timeout(coro, name: str, timeout: float):
        """带超时的关闭操作"""
        try:
            await asyncio.wait_for(coro, timeout=timeout)
            print(f"{name} closed")
        except asyncio.TimeoutError:
            print(f"WARNING: {name} shutdown timed out after {timeout}s")
        except Exception as e:
            print(f"Error closing {name}: {e}")

    # 1. 关闭 HTTP 客户端
    from app.providers.llm.litellm import close_http_client
    await _shutdown_with_timeout(close_http_client(), "HTTP client", shutdown_timeout)

    # 2. 关闭 Embedding 线程池
    from app.agent.memory.embedding import shutdown_embedding
    await _shutdown_with_timeout(shutdown_embedding(), "Embedding executor", shutdown_timeout)

    # 3. 关闭 LipSync 线程池
    from app.realtime.lipsync_service import shutdown_lipsync
    await _shutdown_with_timeout(shutdown_lipsync(), "LipSync executor", shutdown_timeout)

    # 4. 关闭定时任务调度器
    if settings.SCHEDULER_ENABLED:
        scheduler = get_scheduler()
        if scheduler.is_running:
            await _shutdown_with_timeout(scheduler.stop(), "Scheduler", shutdown_timeout)

    # 5. 关闭 Channel 层
    channel_manager = get_channel_manager()
    if channel_manager.is_running():
        await _shutdown_with_timeout(channel_manager.stop(), "Channel layer", shutdown_timeout)
        reset_channel_manager()


    # 6. 关闭实时语音流（Agent 服务 + 情绪/好感度调度器）
    try:
        await _shutdown_with_timeout(realtime_manager.stop(), "Realtime voice stream", shutdown_timeout)
        reset_realtime_manager()
    except Exception:
        pass  # 模块可能未启动

    # 7. 关闭 Agent 服务（兼容兜底）
    await _shutdown_with_timeout(stop_agent_service(), "Agent service", shutdown_timeout)

    # 8. 关闭 Redis 连接池
    await _shutdown_with_timeout(close_redis_pool(), "Redis pool", shutdown_timeout)

    # 9. 关闭 Stone 数据库
    await _shutdown_with_timeout(close_database(), "Stone database", shutdown_timeout)


app = FastAPI(
    title="MMD Agent Backend",
    description="MMD 虚拟人动作管理 + 情绪 AI Agent 后端",
    version="1.0.0",
    lifespan=lifespan,
)


# ===== 全局异常处理器 =====

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器 - 捕获所有未处理的异常"""
    from app.errors import ErrorCode, ERROR_MESSAGES
    from loguru import logger
    
    # 记录异常日志
    logger.error(f"[GlobalException] {exc.__class__.__name__}: {str(exc)}")
    
    # 返回标准错误响应
    return JSONResponse(
        status_code=500,
        content={
            "code": ErrorCode.UNKNOWN_ERROR,
            "message": ERROR_MESSAGES.get(ErrorCode.UNKNOWN_ERROR, "Unknown error"),
            "detail": str(exc)[:200],  # 限制详情长度
        }
    )


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """应用异常处理器 - 处理自定义异常"""
    # 记录异常日志
    logger.warning(f"[AppException] {exc.code}: {exc.message}")
    
    # 根据错误类型确定 HTTP 状态码
    status_code = 500
    if exc.code.startswith("E1"):  # 通用错误
        if exc.code == "E1003":  # NOT_FOUND
            status_code = 404
        elif exc.code == "E1004":  # PERMISSION_DENIED
            status_code = 403
        elif exc.code == "E1001":  # INVALID_REQUEST
            status_code = 400
    elif exc.code.startswith("E5"):  # 资源错误
        status_code = 404
    elif exc.code.startswith("E6"):  # 文件错误
        if exc.code == "E6001":  # FILE_TOO_LARGE
            status_code = 413
        else:
            status_code = 400
    
    response = exc.to_response()
    return JSONResponse(
        status_code=status_code,
        content={
            "code": response.code,
            "message": response.message,
            "detail": response.detail,
            "data": response.data,
        }
    )


# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(motions_router)
app.include_router(tags_router)
app.include_router(rt_router)
app.include_router(vmd_upload_router)

# 挂载前端静态文件
DIST_DIR = "dist"
DIST_VMD_DIR = "dist-vmd2sql"

# 主前端静态文件
if os.path.isdir(DIST_DIR):
    app.mount("/static", StaticFiles(directory=DIST_DIR), name="static")
    print(f"Static files mounted from: {DIST_DIR}")
else:
    print(f"WARNING: Static files directory '{DIST_DIR}' not found")

# 角色模型静态文件（挂载到 /characters 路径）
CHARACTERS_DIR = "config/characters"
if os.path.isdir(CHARACTERS_DIR):
    app.mount("/characters", StaticFiles(directory=CHARACTERS_DIR), name="characters")
    print(f"Characters static files mounted from: {CHARACTERS_DIR}")
else:
    print(f"WARNING: Characters directory '{CHARACTERS_DIR}' not found")

# VMD 上传页面静态文件（挂载到 /vmd 路径）
if os.path.isdir(DIST_VMD_DIR):
    app.mount("/vmd/static", StaticFiles(directory=DIST_VMD_DIR), name="vmd-static")
    print(f"VMD static files mounted from: {DIST_VMD_DIR}")
else:
    print(f"WARNING: VMD static files directory '{DIST_VMD_DIR}' not found")


@app.get("/health")
async def health_check():
    """健康检查 - 检查所有依赖服务状态"""
    from sqlalchemy import text
    from app.stone import get_database, get_redis_pool
    import time

    health_status = {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": time.time(),
        "components": {}
    }

    all_healthy = True

    # 1. 检查 PostgreSQL 数据库（Stone）
    try:
        db = get_database()
        async with db.get_session() as session:
            await session.execute(text("SELECT 1"))
        health_status["components"]["database"] = {"status": "healthy", "type": "postgresql"}
    except Exception as e:
        all_healthy = False
        health_status["components"]["database"] = {"status": "unhealthy", "error": str(e)[:100]}

    # 2. 检查 Redis（Stone）
    try:
        redis_pool = get_redis_pool()
        await redis_pool.get_client().ping()
        health_status["components"]["redis"] = {"status": "healthy"}
    except Exception as e:
        all_healthy = False
        health_status["components"]["redis"] = {"status": "unhealthy", "error": str(e)[:100]}

    # 3. 检查 Agent 服务状态
    try:
        from app.realtime.agent_service import get_agent_service
        service = get_agent_service()
        if service and service.is_running:
            health_status["components"]["agent_service"] = {
                "status": "healthy",
                "initialized": service.is_initialized,
                "running": service.is_running
            }
        else:
            health_status["components"]["agent_service"] = {
                "status": "starting",
                "initialized": service is not None and service.is_initialized,
                "running": False
            }
    except Exception as e:
        health_status["components"]["agent_service"] = {"status": "unhealthy", "error": str(e)[:100]}

    # 设置整体状态
    if not all_healthy:
        health_status["status"] = "degraded"

    return health_status


@app.get("/ready")
async def readiness_check():
    """就绪检查 - 检查服务是否完全启动并准备好接收请求"""
    from app.realtime.agent_service import get_agent_service
    from app.realtime.init_gate import get_init_gate
    
    gate = get_init_gate()
    service = get_agent_service()
    
    # 检查初始化门控是否全部完成
    gate_ready = gate.is_all_ready()
    
    # 检查 Agent 服务是否运行
    service_ready = service is not None and service.is_running
    
    if gate_ready and service_ready:
        return {"ready": True, "message": "Service is ready"}
    else:
        missing = []
        if not gate_ready:
            missing.append("init_gate")
        if not service_ready:
            missing.append("agent_service")
        return {"ready": False, "missing": missing}


@app.get("/live")
async def liveness_check():
    """存活检查 - 简单检查进程是否存活"""
    return {"alive": True}


@app.get("/")
async def root():
    """根路径 - 返回前端 index.html"""
    index_path = os.path.join(DIST_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {
        "message": "MMD 动作管理系统 API",
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/vmd")
async def vmd_root():
    """VMD 上传页面根路径 - 返回 vmd/index.html"""
    index_path = os.path.join(DIST_VMD_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"error": "VMD page not found", "hint": "Please build vmd2sql frontend first"}


@app.get("/vmd/{full_path:path}")
async def serve_vmd_spa(full_path: str):
    """VMD SPA 路由回退 - 支持前端路由"""
    static_file_path = os.path.join(DIST_VMD_DIR, full_path)
    if os.path.isfile(static_file_path):
        return FileResponse(static_file_path)
    # 回退到 index.html（SPA 路由）
    index_path = os.path.join(DIST_VMD_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"error": "VMD page not found"}


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """SPA 路由回退 - 支持前端路由"""
    static_file_path = os.path.join(DIST_DIR, full_path)
    if os.path.isfile(static_file_path):
        return FileResponse(static_file_path)
    index_path = os.path.join(DIST_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"error": "Not found"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        lifespan="on",
    )