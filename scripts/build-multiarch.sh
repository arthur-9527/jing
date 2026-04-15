#!/bin/bash
# ========================================
# Jing Multi-Platform Docker Build Script
# ========================================
# 本地多平台 Docker 镜像构建脚本
# 支持: linux/amd64, linux/arm64
#
# 使用方法:
#   ./scripts/build-multiarch.sh [options]
#
# 选项:
#   -j, --jing        构建 Jing 镜像
#   -p, --postgres    构建 PostgreSQL 镜像
#   -a, --all         构建所有镜像 (默认)
#   -P, --push        构建后推送到 Docker Hub
#   -t, --tag TAG     指定镜像标签 (默认: latest)
#   -c, --ci          使用 Dockerfile.ci (跳过前端编译，用于 CI)
#   -h, --help        显示帮助信息
#
# 前置要求:
#   1. 安装 docker buildx: docker buildx install
#   2. 创建并使用 builder: docker buildx create --name multiarch --use
#   3. 登录 Docker Hub: docker login
# ========================================

set -e

# ========================================
# 配置
# ========================================
JING_IMAGE="hostname9527/jing"
POSTGRES_IMAGE="hostname9527/jing-postgres"
PLATFORMS="linux/amd64,linux/arm64"
TAG="latest"
PUSH=false
BUILD_JING=false
BUILD_POSTGRES=false
USE_CI_DOCKERFILE=false

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ========================================
# 帮助信息
# ========================================
show_help() {
    echo "Jing Multi-Platform Docker Build Script"
    echo ""
    echo "使用方法: $0 [options]"
    echo ""
    echo "选项:"
    echo "  -j, --jing        构建 Jing 镜像"
    echo "  -p, --postgres    构建 PostgreSQL 镜像"
    echo "  -a, --all         构建所有镜像 (默认)"
    echo "  -P, --push        构建后推送到 Docker Hub"
    echo "  -t, --tag TAG     指定镜像标签 (默认: latest)"
    echo "  -c, --ci          使用 Dockerfile.ci (跳过前端编译)"
    echo "  -h, --help        显示帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                         # 构建所有镜像，不推送"
    echo "  $0 -j -P                   # 构建 Jing 镜像并推送"
    echo "  $0 -p -t v1.0.0            # 构建 PostgreSQL 镜像，标签 v1.0.0"
    echo "  $0 --all --push --tag v2.0.0  # 构建所有镜像，标签 v2.0.0，推送"
    echo ""
    echo "前置要求:"
    echo "  1. docker buildx install"
    echo "  2. docker buildx create --name multiarch --use"
    echo "  3. docker login"
}

# ========================================
# 参数解析
# ========================================
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -j|--jing)
                BUILD_JING=true
                shift
                ;;
            -p|--postgres)
                BUILD_POSTGRES=true
                shift
                ;;
            -a|--all)
                BUILD_JING=true
                BUILD_POSTGRES=true
                shift
                ;;
            -P|--push)
                PUSH=true
                shift
                ;;
            -t|--tag)
                TAG="$2"
                shift 2
                ;;
            -c|--ci)
                USE_CI_DOCKERFILE=true
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                echo -e "${RED}未知选项: $1${NC}"
                show_help
                exit 1
                ;;
        esac
    done

    # 默认构建所有
    if [[ "$BUILD_JING" == "false" && "$BUILD_POSTGRES" == "false" ]]; then
        BUILD_JING=true
        BUILD_POSTGRES=true
    fi
}

# ========================================
# 检查前置条件
# ========================================
check_prerequisites() {
    echo -e "${BLUE}检查前置条件...${NC}"
    
    # 检查 Docker
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}错误: Docker 未安装${NC}"
        exit 1
    fi
    
    # 检查 buildx
    if ! docker buildx version &> /dev/null; then
        echo -e "${RED}错误: docker buildx 未安装${NC}"
        echo -e "${YELLOW}请运行: docker buildx install${NC}"
        exit 1
    fi
    
    # 检查 builder
    CURRENT_BUILDER=$(docker buildx inspect 2>/dev/null | grep "Name:" | awk '{print $2}')
    if [[ -z "$CURRENT_BUILDER" ]]; then
        echo -e "${YELLOW}未找到 buildx builder，正在创建...${NC}"
        docker buildx create --name multiarch --use
    fi
    
    # 如果要推送，检查登录状态
    if [[ "$PUSH" == "true" ]]; then
        if ! docker info 2>/dev/null | grep -q "Username"; then
            echo -e "${YELLOW}请先登录 Docker Hub:${NC}"
            docker login
        fi
    fi
    
    # 创建 buildx 缓存目录
    mkdir -p /tmp/.buildx-cache /tmp/.buildx-cache-new
    
    echo -e "${GREEN}前置条件检查通过${NC}"
    echo ""
}

# ========================================
# 构建 Jing 镜像
# ========================================
build_jing() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}构建 Jing 镜像${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo -e "${YELLOW}镜像: ${JING_IMAGE}:${TAG}${NC}"
    echo -e "${YELLOW}平台: ${PLATFORMS}${NC}"
    echo ""
    
    PUSH_FLAG=""
    if [[ "$PUSH" == "true" ]]; then
        PUSH_FLAG="--push"
        echo -e "${GREEN}构建后将推送到 Docker Hub${NC}"
    else
        echo -e "${YELLOW}注意: 仅构建本地镜像，不推送${NC}"
        echo -e "${YELLOW}如需推送，请使用 -P 或 --push 参数${NC}"
    fi
    
    # 获取脚本所在目录
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
    
    # 根据 CI 模式选择 Dockerfile 和 context
    if [[ "$USE_CI_DOCKERFILE" == "true" ]]; then
        # CI 模式：使用 Dockerfile.ci，context 为当前目录
        if [[ -f "${PROJECT_ROOT}/Dockerfile.ci" ]]; then
            DOCKERFILE_PATH="${PROJECT_ROOT}/Dockerfile.ci"
            CONTEXT_PATH="${PROJECT_ROOT}"
            echo -e "${GREEN}使用 CI 模式: Dockerfile.ci${NC}"
        else
            echo -e "${RED}错误: 找不到 Dockerfile.ci${NC}"
            exit 1
        fi
    else
        # 普通模式：使用完整 Dockerfile，context 为父目录
        if [[ ! -f "${PROJECT_ROOT}/jing/Dockerfile" ]]; then
            # 可能是在 jing 目录下
            if [[ -f "${PROJECT_ROOT}/Dockerfile" ]]; then
                DOCKERFILE_PATH="${PROJECT_ROOT}/Dockerfile"
                CONTEXT_PATH="$(dirname "$PROJECT_ROOT")"
            else
                echo -e "${RED}错误: 找不到 Dockerfile${NC}"
                exit 1
            fi
        else
            DOCKERFILE_PATH="${PROJECT_ROOT}/jing/Dockerfile"
            CONTEXT_PATH="$(dirname "$PROJECT_ROOT")"
        fi
    fi
    
    echo -e "${BLUE}构建上下文: ${CONTEXT_PATH}${NC}"
    echo -e "${BLUE}Dockerfile: ${DOCKERFILE_PATH}${NC}"
    echo ""
    
    # 构建镜像
    docker buildx build \
        --platform "${PLATFORMS}" \
        --tag "${JING_IMAGE}:${TAG}" \
        ${PUSH_FLAG:+$PUSH_FLAG} \
        --cache-from type=local,src=/tmp/.buildx-cache \
        --cache-to type=local,dest=/tmp/.buildx-cache-new \
        -f "${DOCKERFILE_PATH}" \
        "${CONTEXT_PATH}"
    
    echo -e "${GREEN}Jing 镜像构建完成${NC}"
    echo ""
}

# ========================================
# 构建 PostgreSQL 镜像
# ========================================
build_postgres() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}构建 PostgreSQL 镜像${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo -e "${YELLOW}镜像: ${POSTGRES_IMAGE}:${TAG}${NC}"
    echo -e "${YELLOW}平台: ${PLATFORMS}${NC}"
    echo ""
    
    PUSH_FLAG=""
    if [[ "$PUSH" == "true" ]]; then
        PUSH_FLAG="--push"
        echo -e "${GREEN}构建后将推送到 Docker Hub${NC}"
    fi
    
    # 获取脚本所在目录
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
    
    # 检查 Dockerfile
    if [[ ! -f "${PROJECT_ROOT}/Dockerfile.postgres" ]]; then
        echo -e "${RED}错误: 找不到 Dockerfile.postgres${NC}"
        exit 1
    fi
    
    echo -e "${BLUE}构建上下文: ${PROJECT_ROOT}${NC}"
    echo -e "${BLUE}Dockerfile: ${PROJECT_ROOT}/Dockerfile.postgres${NC}"
    echo ""
    
    # 构建镜像
    docker buildx build \
        --platform "${PLATFORMS}" \
        --tag "${POSTGRES_IMAGE}:${TAG}" \
        ${PUSH_FLAG:+$PUSH_FLAG} \
        --cache-from type=local,src=/tmp/.buildx-cache \
        --cache-to type=local,dest=/tmp/.buildx-cache-new \
        -f "${PROJECT_ROOT}/Dockerfile.postgres" \
        "${PROJECT_ROOT}"
    
    echo -e "${GREEN}PostgreSQL 镜像构建完成${NC}"
    echo ""
}

# ========================================
# 清理缓存
# ========================================
cleanup_cache() {
    if [ -d /tmp/.buildx-cache ]; then
        rm -rf /tmp/.buildx-cache
    fi
    if [ -d /tmp/.buildx-cache-new ]; then
        mv /tmp/.buildx-cache-new /tmp/.buildx-cache
    fi
}

# ========================================
# 主函数
# ========================================
main() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Jing Multi-Platform Docker Build${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    
    parse_args "$@"
    check_prerequisites
    
    START_TIME=$(date +%s)
    
    if [[ "$BUILD_JING" == "true" ]]; then
        build_jing
    fi
    
    if [[ "$BUILD_POSTGRES" == "true" ]]; then
        build_postgres
    fi
    
    cleanup_cache
    
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}构建完成!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "${BLUE}耗时: ${DURATION} 秒${NC}"
    echo ""
    
    if [[ "$PUSH" == "true" ]]; then
        echo -e "${GREEN}镜像已推送到 Docker Hub${NC}"
        echo -e "${BLUE}拉取命令:${NC}"
        if [[ "$BUILD_JING" == "true" ]]; then
            echo -e "  docker pull ${JING_IMAGE}:${TAG}"
        fi
        if [[ "$BUILD_POSTGRES" == "true" ]]; then
            echo -e "  docker pull ${POSTGRES_IMAGE}:${TAG}"
        fi
    else
        echo -e "${YELLOW}镜像仅构建在本地${NC}"
        echo -e "${YELLOW}注意: 多平台镜像需要推送到仓库后才能在其他平台拉取${NC}"
        echo -e "${BLUE}推送命令:${NC}"
        echo -e "  $0 -P -t ${TAG}"
    fi
}

main "$@"