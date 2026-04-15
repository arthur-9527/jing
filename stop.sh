#!/bin/bash
# ========================================
# Jing 停止脚本
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

# 解析参数
CLEAN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --clean) CLEAN=true; shift ;;
        *) print_error "未知参数: $1"; exit 1 ;;
    esac
done

print_info "========== Jing 停止脚本 =========="

# 停止服务
print_info "停止服务..."
docker-compose down

print_success "服务已停止"

# 清理数据（可选）
if [ "$CLEAN" = true ]; then
    print_warning "清理 Docker 数据卷..."
    docker-compose down -v
    print_success "数据卷已清理"
fi

# 显示状态
echo ""
docker-compose ps