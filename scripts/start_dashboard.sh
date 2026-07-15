#!/bin/bash
# A股量化选股系统 - Streamlit仪表盘启动脚本

cd "$(dirname "$0")/.." || exit 1

PYTHON_BIN="${PYTHON_BIN:-python3}"
DASHBOARD_HOST="${DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8501}"

echo "🚀 启动 A股量化选股系统监控仪表盘..."
echo "本机预览地址: http://${DASHBOARD_HOST}:${DASHBOARD_PORT}"
echo "公网部署建议: 备案域名 + Nginx/Caddy 反向代理 + HTTPS + 鉴权"
echo "按 Ctrl+C 停止运行"
echo ""

if ! "$PYTHON_BIN" -c "import streamlit" >/dev/null 2>&1; then
  echo "未检测到 streamlit 运行依赖。"
  echo "请先运行: $PYTHON_BIN -m pip install -e \".[web]\""
  exit 1
fi

PORT_CHECK_OUTPUT=$(
  "$PYTHON_BIN" scripts/open_dashboard.py \
    --host "$DASHBOARD_HOST" \
    --port "$DASHBOARD_PORT" \
    --check-port 2>&1
)
PORT_CHECK_STATUS=$?
if [ "$PORT_CHECK_STATUS" -ne 0 ]; then
  printf '%s\n' "$PORT_CHECK_OUTPUT" >&2
  exit "$PORT_CHECK_STATUS"
fi
if [ "$PORT_CHECK_OUTPUT" = "port_status=current" ]; then
  echo "当前 AQSP Streamlit 看板已在运行，不重复启动。"
  exit 0
fi

exec "$PYTHON_BIN" -m streamlit run src/aqsp/web/dashboard.py \
  --server.address "$DASHBOARD_HOST" \
  --server.port "$DASHBOARD_PORT" \
  --server.headless true
