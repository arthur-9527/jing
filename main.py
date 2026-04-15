"""Jing - 主应用入口"""

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
from app.database import init_db, close_db
from app.agent.db.connection import init_db as init_agent_db, close_pool as close_agent_pool
from app.routers import motions_router, tags_router, ws_router, agent_router
from app.routers.vmd_upload import router as vmd_upload_router
from app.services.agent_service import start_agent_service, stop_agent_service
from app.services.embedding_service import preload_embedding_model
from app.scheduler import get_scheduler

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

    # 初始化 SQLAlchemy 数据库（motion 表）
    try:
        await init_db()
        print("Database initialized")
    except Exception as e:
        print(f"WARNING: Database init failed: {e}")

    # 初始化 Agent 数据库 schema（统一走 SQLAlchemy engine，创建 agent 表）
    try:
        await init_agent_db()
        print("Agent database schema initialized")
    except Exception as e:
        print(f"WARNING: Agent DB init failed: {e}")

    # 预加载 Embedding 模型（启动时加载到内存）
    print("Loading Embedding model...")
    import time
    from app.agent.memory.embedding import get_embedding
    try:
        preload_embedding_model()
        print("Embedding model loaded")
        
        # 预热调用 - 执行一次实际推理并打印时间
        t0 = time.time()
        test_embedding = await get_embedding("系统初始化测试")
        elapsed = (time.time() - t0) * 1000
        print(f"Embedding warmup: {elapsed:.1f}ms, dim={len(test_embedding)}")
    except Exception as e:
        print(f"WARNING: Embedding model load failed: {e}")

    # 启动 Agent 服务（初始化 Pipeline）
    print("Starting Agent service...")
    try:
        await start_agent_service()
        print("Agent service started")
    except Exception as e:
        print(f"Failed to start Agent service: {e}")
        import traceback
        traceback.print_exc()

    # 启动定时任务调度器
    if settings.SCHEDULER_ENABLED:
        print("Starting Scheduler...")
        try:
            scheduler = get_scheduler()
            await scheduler.start()
            print(f"Scheduler started, jobs: {len(scheduler.get_jobs())}")
        except Exception as e:
            print(f"WARNING: Scheduler start failed: {e}")

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
    from app.agent.llm.providers.litellm import close_http_client
    await _shutdown_with_timeout(close_http_client(), "HTTP client", shutdown_timeout)

    # 2. 关闭 Embedding 线程池
    from app.agent.memory.embedding import shutdown_embedding
    await _shutdown_with_timeout(shutdown_embedding(), "Embedding executor", shutdown_timeout)

    # 3. 关闭 LipSync 线程池
    from app.services.lipsync_service import shutdown_lipsync
    await _shutdown_with_timeout(shutdown_lipsync(), "LipSync executor", shutdown_timeout)

    # 4. 关闭定时任务调度器
    if settings.SCHEDULER_ENABLED:
        scheduler = get_scheduler()
        if scheduler.is_running:
            await _shutdown_with_timeout(scheduler.stop(), "Scheduler", shutdown_timeout)

    # 5. 关闭 Agent 服务
    await _shutdown_with_timeout(stop_agent_service(), "Agent service", shutdown_timeout)

    # 6. 关闭 Agent 数据库连接池
    await _shutdown_with_timeout(close_agent_pool(), "Agent DB pool", shutdown_timeout)

    # 7. 关闭主数据库
    await _shutdown_with_timeout(close_db(), "Database", shutdown_timeout)


app = FastAPI(
    title="Jing",
    description="Jing - AI Agent 后端",
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
app.include_router(ws_router)
app.include_router(agent_router)
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
    from app.database import get_db_session
    from redis import asyncio as aioredis
    from app.config import settings
    import time
    
    health_status = {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": time.time(),
        "components": {}
    }
    
    all_healthy = True
    
    # 1. 检查 PostgreSQL 数据库
    try:
        async with get_db_session() as db:
            await db.execute("SELECT 1")
        health_status["components"]["database"] = {"status": "healthy", "type": "postgresql"}
    except Exception as e:
        all_healthy = False
        health_status["components"]["database"] = {"status": "unhealthy", "error": str(e)[:100]}
    
    # 2. 检查 Redis
    try:
        redis = aioredis.from_url(settings.REDIS_URL)
        await redis.ping()
        await redis.close()
        health_status["components"]["redis"] = {"status": "healthy"}
    except Exception as e:
        all_healthy = False
        health_status["components"]["redis"] = {"status": "unhealthy", "error": str(e)[:100]}
    
    # 3. 检查 Agent 服务状态
    try:
        from app.services.agent_service import get_agent_service
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
    
    # 4. 检查 Agent 数据库
    try:
        from app.agent.db.connection import get_agent_db_session
        async with get_agent_db_session() as db:
            await db.execute("SELECT 1")
        health_status["components"]["agent_database"] = {"status": "healthy"}
    except Exception as e:
        health_status["components"]["agent_database"] = {"status": "unhealthy", "error": str(e)[:100]}
    
    # 设置整体状态
    if not all_healthy:
        health_status["status"] = "degraded"
    
    return health_status


@app.get("/ready")
async def readiness_check():
    """就绪检查 - 检查服务是否完全启动并准备好接收请求"""
    from app.services.agent_service import get_agent_service
    from app.services.init_gate import get_init_gate
    
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