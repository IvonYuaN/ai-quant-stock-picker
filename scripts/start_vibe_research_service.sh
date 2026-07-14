#!/usr/bin/env bash
# Start the persistent Vibe-Research systemd target; never daemonize with nohup.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
TARGET_UNIT="${AQSP_VIBE_SYSTEMD_TARGET:-aqsp-vibe-research.target}"
ENV_FILE="${AQSP_VIBE_ENV_FILE:-/etc/aqsp/vibe-research.env}"
BUILD="false"

usage() {
    cat <<'EOF'
用法: scripts/start_vibe_research_service.sh [--build] [--env-file PATH]

启动正式 systemd target：API 127.0.0.1:8900、Vite preview 127.0.0.1:5899。
脚本不会使用 nohup、后台孤儿进程，也不会杀掉端口上的未知进程。
EOF
}

while (($# > 0)); do
    case "$1" in
        --build) BUILD="true" ;;
        --env-file) ENV_FILE="${2:?缺少 --env-file 参数}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

[[ -r "$ENV_FILE" ]] || {
    echo "环境文件不可读: ${ENV_FILE}" >&2
    echo "请复制 deploy/systemd/aqsp-vibe-research.env.example 后填写快照路径。" >&2
    exit 1
}

"$SYSTEMCTL_BIN" daemon-reload
"$SYSTEMCTL_BIN" enable "$TARGET_UNIT"

if "$SYSTEMCTL_BIN" is-active --quiet "$TARGET_UNIT"; then
    exec "${PROJECT_ROOT}/scripts/health_vibe_research.sh" \
        --env-file "$ENV_FILE" \
        --systemd-unit "$TARGET_UNIT"
fi

if [[ "$BUILD" == "true" ]]; then
    command -v npm >/dev/null 2>&1 || {
        echo "缺少 npm，无法构建前端。" >&2
        exit 1
    }
    (cd "${PROJECT_ROOT}/frontend" && npm run build)
fi

"${PROJECT_ROOT}/scripts/health_vibe_research.sh" \
    --env-file "$ENV_FILE" \
    --preflight-only \
    --port-guard

"$SYSTEMCTL_BIN" start "$TARGET_UNIT"

exec "${PROJECT_ROOT}/scripts/health_vibe_research.sh" \
    --env-file "$ENV_FILE" \
    --systemd-unit "$TARGET_UNIT"
