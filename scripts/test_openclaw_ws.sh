#!/bin/bash
# OpenClaw WebSocket 服务测试脚本

set -e

echo "=========================================="
echo "OpenClaw WebSocket 服务测试"
echo "=========================================="
echo ""

# 检查环境
echo "1️⃣  检查环境..."
echo "   ✓ Redis: $(redis-cli ping 2>/dev/null || echo '❌ 未运行')"

# 检查 OpenClaw Gateway
echo "   ✓ OpenClaw Gateway: "
if nc -z 127.0.0.1 18789 2>/dev/null; then
    echo "     ✅ 运行中 (ws://127.0.0.1:18789)"
else
    echo "     ❌ 未运行"
    echo ""
    echo "❌ 请先启动 OpenClaw Gateway:"
    echo "   docker run -d -p 18789:18789 openclaw/gateway"
    echo "   或参考你的 OpenClaw 部署方式"
    exit 1
fi

# 检查身份文件
echo "   ✓ 设备身份文件:"
IDENTITY_FILE="${HOME}/.openclaw/pipecat_identity.json"
if [ -f "$IDENTITY_FILE" ]; then
    echo "     ✅ $IDENTITY_FILE"
else
    echo "     ⚠️  不存在: $IDENTITY_FILE"
    echo ""
    echo "💡 提示: 如果 OpenClaw Gateway 未启用设备认证，可以忽略"
fi

# 检查 LLM 服务
echo "   ✓ LLM API (用于二次处理):"
LLM_BASE_URL=$(grep OPENCLAW_LLM_POST_PROCESS_BASE_URL .env 2>/dev/null | cut -d'=' -f2)
LLM_BASE_URL=${LLM_BASE_URL:-"http://43.153.150.28:4000/v1"}
LLM_HOST=$(echo $LLM_BASE_URL | sed -E 's|https?://([^:/]+).*|\1|')
LLM_PORT=$(echo $LLM_BASE_URL | sed -E 's|.*:([0-9]+).*|\1|')

if curl -s -o /dev/null -w "%{http_code}" "http://${LLM_HOST}:${LLM_PORT}/health" >/dev/null 2>&1 || \
   curl -s -o /dev/null -w "%{http_code}" "$LLM_BASE_URL/models" >/dev/null 2>&1; then
    echo "     ✅ 可访问 ($LLM_BASE_URL)"
else
    echo "     ⚠️  可能不可访问 ($LLM_BASE_URL)"
    echo "     💡 这是可选的，二次处理失败时会使用 OpenClaw 原始结果"
fi

echo ""
echo "=========================================="
echo "2️⃣  检查配置文件..."
echo ""

# 检查必需的配置项
echo "检查 .env 配置:"
REQUIRED_CONFIG=(
    "OPENCLAW_WS_URL"
    "OPENCLAW_API_KEY"
    "OPENCLAW_LLM_POST_PROCESS_MODEL"
    "OPENCLAW_LLM_POST_PROCESS_BASE_URL"
)

for config in "${REQUIRED_CONFIG[@]}"; do
    value=$(grep "^${config}=" .env 2>/dev/null | cut -d'=' -f2)
    if [ -n "$value" ]; then
        echo "  ✓ $config=$value"
    else
        echo "  ⚠️  $config 未配置"
    fi
done

echo ""
echo "=========================================="
echo "3️⃣  运行测试..."
echo ""

# 激活 conda 环境
echo "激活 conda 环境: backend"
eval "$(conda shell.bash hook)"
conda activate backend

# 运行测试
echo "启动测试脚本..."
python tests/test_openclaw_post_processing.py

echo ""
echo "=========================================="
echo "✅ 测试完成！"
echo "=========================================="
