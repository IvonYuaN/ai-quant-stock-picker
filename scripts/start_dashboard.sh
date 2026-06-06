#!/bin/bash
# A股量化选股系统 - Streamlit仪表盘启动脚本

cd "$(dirname "$0")/.." || exit 1

echo "🚀 启动 A股量化选股系统监控仪表盘..."
echo "本机预览地址: http://127.0.0.1:8501"
echo "公网部署建议: 备案域名 + Nginx/Caddy 反向代理 + HTTPS + 鉴权"
echo "按 Ctrl+C 停止运行"
echo ""

streamlit run src/aqsp/web/dashboard.py --server.address 127.0.0.1 --server.port 8501
