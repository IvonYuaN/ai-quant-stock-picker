#!/usr/bin/env bash
# Stop only the AQSP systemd target; do not kill by port or process name.
set -euo pipefail

SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
TARGET_UNIT="${AQSP_VIBE_SYSTEMD_TARGET:-aqsp-vibe-research.target}"
API_UNIT="aqsp-vibe-research-api.service"
PREVIEW_UNIT="aqsp-vibe-research-preview.service"
DISABLE="false"

usage() {
    cat <<'EOF'
用法: scripts/stop_vibe_research_service.sh [--disable]

停止 AQSP systemd target。默认保留开机启动配置；--disable 同时取消开机启动。
EOF
}

while (($# > 0)); do
    case "$1" in
        --disable) DISABLE="true" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if "$SYSTEMCTL_BIN" is-active --quiet "$TARGET_UNIT" || \
    "$SYSTEMCTL_BIN" is-active --quiet "$API_UNIT" || \
    "$SYSTEMCTL_BIN" is-active --quiet "$PREVIEW_UNIT"; then
    "$SYSTEMCTL_BIN" stop "$TARGET_UNIT" "$PREVIEW_UNIT" "$API_UNIT"
    echo "已停止 ${TARGET_UNIT} 及其子 service"
else
    echo "${TARGET_UNIT} 未运行"
fi

if [[ "$DISABLE" == "true" ]]; then
    "$SYSTEMCTL_BIN" disable "$TARGET_UNIT"
    echo "已取消 ${TARGET_UNIT} 的开机启动"
fi
