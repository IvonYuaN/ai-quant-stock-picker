#!/usr/bin/env bash
# Roll back the repository to an existing Git ref and restart the systemd target.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
TARGET_UNIT="${AQSP_VIBE_SYSTEMD_TARGET:-aqsp-vibe-research.target}"
API_UNIT="aqsp-vibe-research-api.service"
PREVIEW_UNIT="aqsp-vibe-research-preview.service"
ENV_FILE="${AQSP_VIBE_ENV_FILE:-/etc/aqsp/vibe-research.env}"
TARGET_REF="${1:-}"

usage() {
    cat <<'EOF'
用法: scripts/rollback_vibe_research.sh <已存在的 Git ref> [--env-file PATH]

要求工作树干净、目标 ref 已在本地仓库中存在。脚本会停止 target、切换到目标
提交、启动并健康检查；若目标版本不健康，会自动恢复原提交并再次检查。
EOF
}

if [[ "$TARGET_REF" == "-h" || "$TARGET_REF" == "--help" || -z "$TARGET_REF" ]]; then
    usage >&2
    exit 2
fi
shift
while (($# > 0)); do
    case "$1" in
        --env-file) ENV_FILE="${2:?缺少 --env-file 参数}"; shift ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

git -C "$PROJECT_ROOT" diff --quiet || {
    echo "工作树有未提交修改，拒绝回滚以避免覆盖服务器脏改。" >&2
    exit 1
}
git -C "$PROJECT_ROOT" diff --cached --quiet || {
    echo "暂存区有未提交修改，拒绝回滚。" >&2
    exit 1
}
OLD_REF="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
git -C "$PROJECT_ROOT" rev-parse --verify "${TARGET_REF}^{commit}" >/dev/null \
    || { echo "目标 Git ref 不存在: ${TARGET_REF}" >&2; exit 1; }

ROLLBACK_LOG_DIR="${PROJECT_ROOT}/logs/vibe-research"
mkdir -p "$ROLLBACK_LOG_DIR"
printf '%s old=%s target=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$OLD_REF" "$TARGET_REF" \
    >>"${ROLLBACK_LOG_DIR}/rollback.log"

restore_old() {
    echo "目标版本健康检查失败，恢复 ${OLD_REF}" >&2
    git -C "$PROJECT_ROOT" reset --keep "$OLD_REF"
    "$SYSTEMCTL_BIN" daemon-reload
    "$SYSTEMCTL_BIN" start "$TARGET_UNIT"
    "${PROJECT_ROOT}/scripts/health_vibe_research.sh" \
        --env-file "$ENV_FILE" \
        --systemd-unit "$TARGET_UNIT"
}

"$SYSTEMCTL_BIN" stop "$TARGET_UNIT" "$PREVIEW_UNIT" "$API_UNIT"
git -C "$PROJECT_ROOT" reset --keep "$TARGET_REF"
"$SYSTEMCTL_BIN" daemon-reload
"$SYSTEMCTL_BIN" start "$TARGET_UNIT" || { restore_old; exit 1; }

if ! "${PROJECT_ROOT}/scripts/health_vibe_research.sh" \
    --env-file "$ENV_FILE" \
    --systemd-unit "$TARGET_UNIT"; then
    restore_old
    exit 1
fi

echo "AQSP 已回滚到 ${TARGET_REF}。"
