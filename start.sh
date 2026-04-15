#!/bin/bash
# ========================================
# Jing 启动脚本
# ========================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查 Docker
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker 未安装"
        exit 1
    fi
    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose 未安装"
        exit 1
    fi
    print_success "Docker 已就绪"
}

# 检查 .env
check_env() {
    if [ ! -f ".env" ]; then
        print_warning ".env 不存在"
        if [ -f ".env.example" ]; then
            cp .env.example .env
            print_warning "请编辑 .env 文件"
        else
            print_error "请手动创建 .env"
            exit 1
        fi
    fi
    print_success ".env 已存在"
}

# 创建目录
create_dirs() {
    mkdir -p temp logs
    print_success "目录已创建"
}

# 解析参数
BUILD=false
DEV=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --build) BUILD=true; shift ;;
        --dev) DEV=true; shift ;;
        *) print_error "未知参数: $1"; exit 1 ;;
    esac
done

# 主流程
print_info "========== Jing 启动 =========="
check_docker
check_env
create_dirs

if [ "$BUILD" = true ]; then
    print_info "重新构建镜像..."
    docker-compose build --no-cache
fi

if [ "$DEV" = true ]; then
    docker-compose up
else
    docker-compose up -d
    print_success "服务已启动"
    docker-compose ps
    
    print_info "等待服务就绪..."
    for i in {1..120}; do
        if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
            print_success "服务就绪！API: http://localhost:8000/docs"
            exit 0
        fi
        sleep 1
    done
    print_warning "启动超时，请检查日志"
fi