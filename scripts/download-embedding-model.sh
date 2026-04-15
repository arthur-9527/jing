#!/bin/bash
# ========================================
# Embedding 模型下载脚本
# ========================================
# 模型：BAAI/bge-small-zh-v1.5
# 大小：约 100MB
# 
# 使用方法：
#   ./scripts/download-embedding-model.sh
# ========================================

set -e

# 配置
MODEL_NAME="BAAI/bge-small-zh-v1.5"
TARGET_DIR="./models/embedding"

echo "========================================"
echo "Embedding 模型下载脚本"
echo "========================================"
echo "模型: ${MODEL_NAME}"
echo "目标目录: ${TARGET_DIR}"
echo ""

# 检查是否已存在
if [ -d "${TARGET_DIR}" ] && [ -f "${TARGET_DIR}/config.json" ]; then
    echo "[INFO] 模型已存在: ${TARGET_DIR}"
    echo "[INFO] 如需重新下载，请先删除目录: rm -rf ${TARGET_DIR}"
    exit 0
fi

# 创建目录
mkdir -p "${TARGET_DIR}"

echo "[INFO] 开始下载模型..."

# 检查 Python 是否可用
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] 未找到 Python，请先安装 Python 3"
    exit 1
fi

# 检查 huggingface_hub 是否可用
python3 -c "import huggingface_hub" 2>/dev/null || {
    echo "[INFO] 安装 huggingface_hub..."
    pip install huggingface_hub -q
}

# 使用 Python 下载模型
python3 << EOF
from huggingface_hub import snapshot_download
import os

model_name = "${MODEL_NAME}"
target_dir = "${TARGET_DIR}"

print(f"[INFO] 下载模型: {model_name}")
print(f"[INFO] 目标目录: {target_dir}")

try:
    snapshot_download(
        repo_id=model_name,
        local_dir=target_dir,
        local_dir_use_symlinks=False,
        resume_download=True
    )
    print("[SUCCESS] 模型下载完成!")
except Exception as e:
    print(f"[ERROR] 下载失败: {e}")
    exit(1)
EOF

# 验证下载
if [ -f "${TARGET_DIR}/config.json" ] && [ -f "${TARGET_DIR}/model.safetensors" ]; then
    echo ""
    echo "[SUCCESS] 模型下载完成!"
    echo "[INFO] 模型文件:"
    ls -lh "${TARGET_DIR}"
    echo ""
    echo "[INFO] 请在 .env 中配置模型路径:"
    echo "  EMBEDDING_MODEL_PATH=${TARGET_DIR}"
else
    echo "[ERROR] 模型下载不完整，请重新运行脚本"
    rm -rf "${TARGET_DIR}"
    exit 1
fi