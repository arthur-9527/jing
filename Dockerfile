# ========================================
# Jing Dockerfile - 多阶段构建
# ========================================
# 构建命令: docker build -t jing:latest .
# 注意: 需要在 raspi_mmd 父目录下构建，或使用 docker-compose
# ========================================

# ==========================================
# Stage 1: Node.js 前端编译
# ==========================================
FROM node:20-alpine AS frontend-builder

WORKDIR /build

# 复制前端项目
COPY frontend/package*.json ./frontend/
COPY vmd2sql/frontend/package*.json ./vmd2sql/frontend/

# 安装依赖
RUN cd frontend && npm ci
RUN cd vmd2sql/frontend && npm ci

# 复制源代码
COPY frontend ./frontend
COPY vmd2sql/frontend ./vmd2sql/frontend

# 编译前端（输出到 agent_backend/dist 和 agent_backend/dist-vmd2sql）
RUN cd frontend && npm run build
RUN cd vmd2sql/frontend && npm run build

# ==========================================
# Stage 2: Python 运行环境
# ==========================================
FROM python:3.11-slim-bookworm AS runtime

# Labels
LABEL maintainer="Jing Team"
LABEL description="Jing - AI Agent Service"
LABEL version="2.0.0"

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    AUDIO_INPUT_DEVICE_INDEX=-1 \
    AUDIO_OUTPUT_DEVICE_INDEX=-1 \
    PORT=8000 \
    LOG_LEVEL=INFO

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Audio
    libportaudio2 \
    libportaudiocpp0 \
    portaudio19-dev \
    pulseaudio \
    pulseaudio-utils \
    alsa-utils \
    # PostgreSQL
    libpq-dev \
    # OpenCV
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    # FFmpeg
    ffmpeg \
    # Build tools
    gcc \
    g++ \
    make \
    curl \
    # Cleanup
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY jing/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install core dependencies
RUN pip install --no-cache-dir \
    fastapi==0.109.0 \
    uvicorn[standard]==0.27.0 \
    python-multipart==0.0.6 \
    websockets>=12.0 \
    sqlalchemy[asyncio]==2.0.25 \
    asyncpg>=0.29.0 \
    pgvector>=0.3.0 \
    pydantic>=2.10.0 \
    pydantic-settings>=2.6.0 \
    httpx>=0.27.0 \
    python-dotenv>=1.0.0 \
    loguru==0.7.3 \
    tiktoken>=0.7.0 \
    redis

# Install audio/math dependencies
RUN pip install --no-cache-dir \
    numpy>=1.26.0 \
    scipy==1.12.0 \
    sounddevice>=0.5.0

# Install ML dependencies (CPU version for smaller image)
# First install torch from PyTorch CPU index
RUN pip install --no-cache-dir \
    torch>=2.0.0 --index-url https://download.pytorch.org/whl/cpu

# Then install sentence-transformers from PyPI
RUN pip install --no-cache-dir \
    sentence-transformers>=2.2.0

# Install pipecat and related
RUN pip install --no-cache-dir \
    "pipecat-ai==0.0.107" \
    "deepgram-sdk>=6.0.1,<7" \
    "openai>=1.12.0" \
    "cerebras-cloud-sdk>=0.0.1" \
    "apscheduler>=3.10.0" \
    "cryptography==42.0.0"

# Install OpenCV (headless version)
RUN pip install --no-cache-dir opencv-python-headless>=4.9.0

# Copy application code
COPY jing .

# Copy compiled frontend from Stage 1
COPY --from=frontend-builder /build/frontend/dist ./dist
COPY --from=frontend-builder /build/vmd2sql/frontend/dist ./dist-vmd2sql

# Create necessary directories
RUN mkdir -p /app/temp /app/logs /app/models/embedding /tmp/vmd_uploads

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app /tmp/vmd_uploads

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Entry point
ENTRYPOINT ["python", "-m"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]