#!/usr/bin/env bash
# Provision an isolated Vibe-Research runtime and render its systemd units.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_SOURCE_DIR="${PROJECT_ROOT}/deploy/systemd"
SYSTEMD_DEST_DIR="/etc/systemd/system"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
SERVICE_USER="${AQSP_VIBE_USER:-aqsp-vibe}"
SERVICE_GROUP="${AQSP_VIBE_GROUP:-${SERVICE_USER}}"
VENV_DIR="${AQSP_VIBE_VENV_DIR:-${PROJECT_ROOT}/.venv-vibe-research}"
BOOTSTRAP_PYTHON="${AQSP_VIBE_BOOTSTRAP_PYTHON:-python3}"
NPM_BIN="${AQSP_VIBE_NPM_BIN:-}"
ENV_FILE="${AQSP_VIBE_ENV_FILE:-/etc/aqsp/vibe-research.env}"
LOG_DIR="${AQSP_VIBE_LOG_DIR:-${PROJECT_ROOT}/logs/vibe-research}"
DATA_DIR="${AQSP_VIBE_DATA_DIR:-${PROJECT_ROOT}/data/vibe-research}"
SKIP_BUILD="false"
NO_START="false"

usage() {
    cat <<'EOF'
用法: sudo scripts/install_vibe_research_systemd.sh [选项]

默认创建隔离用户 aqsp-vibe、独立虚拟环境 .venv-vibe-research，并安装项目的 [api] extra。
模板会渲染为可直接被 systemd 加载的 unit；不会复用一个缺少 fastapi 的现有 venv。

  --user NAME          运行用户，默认 aqsp-vibe
  --group NAME         运行组，默认与用户同名
  --venv-dir PATH      独立 venv 路径
  --python PATH        创建 venv 的 Python
  --npm PATH           npm 可执行文件路径
  --env-file PATH      systemd EnvironmentFile 路径
  --skip-build         不执行 npm ci/build，只验证已有 frontend/dist
  --no-start           只 provision 和安装 unit，不启动服务
EOF
}

while (($# > 0)); do
    case "$1" in
        --user) SERVICE_USER="${2:?缺少 --user 参数}"; shift ;;
        --group) SERVICE_GROUP="${2:?缺少 --group 参数}"; shift ;;
        --venv-dir) VENV_DIR="${2:?缺少 --venv-dir 参数}"; shift ;;
        --python) BOOTSTRAP_PYTHON="${2:?缺少 --python 参数}"; shift ;;
        --npm) NPM_BIN="${2:?缺少 --npm 参数}"; shift ;;
        --env-file) ENV_FILE="${2:?缺少 --env-file 参数}"; shift ;;
        --skip-build) SKIP_BUILD="true" ;;
        --no-start) NO_START="true" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

[[ "$(id -u)" -eq 0 ]] || { echo "此脚本需要 root，用于创建系统用户和安装 unit。" >&2; exit 1; }
for command_name in useradd groupadd usermod id getent install runuser; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "缺少系统命令: ${command_name}" >&2
        exit 1
    }
done
[[ -f "${PROJECT_ROOT}/pyproject.toml" && -f "${PROJECT_ROOT}/backend/app.py" ]] \
    || { echo "不是可用的 AQSP 项目根目录: ${PROJECT_ROOT}" >&2; exit 1; }
[[ -f "${SYSTEMD_SOURCE_DIR}/aqsp-vibe-research-api.service" ]] \
    || { echo "缺少 systemd 模板: ${SYSTEMD_SOURCE_DIR}" >&2; exit 1; }

if getent passwd "$SERVICE_USER" >/dev/null 2>&1; then
    [[ "$(id -u "$SERVICE_USER")" -ne 0 ]] \
        || { echo "拒绝使用 UID 0 作为 Vibe-Research 隔离用户: ${SERVICE_USER}" >&2; exit 1; }
else
    useradd --system --create-home --home-dir "/var/lib/${SERVICE_USER}" \
        --shell /usr/sbin/nologin --user-group "$SERVICE_USER"
fi

if ! getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
    groupadd --system "$SERVICE_GROUP"
fi
usermod -g "$SERVICE_GROUP" "$SERVICE_USER"

install -d -o root -g "$SERVICE_GROUP" "$(dirname "$ENV_FILE")"
if [[ ! -f "$ENV_FILE" ]]; then
    install -m 0640 -o root -g "$SERVICE_GROUP" \
        "${SYSTEMD_SOURCE_DIR}/aqsp-vibe-research.env.example" "$ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
DATA_DIR="${VR_DATA_DIR:-$DATA_DIR}"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$LOG_DIR" "$DATA_DIR"
SNAPSHOT_PATH="${AQSP_RESEARCH_SURFACE_SNAPSHOT:-}"
SNAPSHOT_INDEX_PATH="$(dirname "$SNAPSHOT_PATH")/home_dashboard_snapshot_index.json"
[[ "$VENV_DIR" = /* && "$ENV_FILE" = /* ]] \
    || { echo "venv 和 EnvironmentFile 必须是绝对路径。" >&2; exit 1; }
[[ "$SNAPSHOT_PATH" = /* ]] \
    || { echo "AQSP_RESEARCH_SURFACE_SNAPSHOT 必须是绝对路径: ${SNAPSHOT_PATH:-未设置}" >&2; exit 1; }
SNAPSHOT_DIR="$(dirname "$SNAPSHOT_PATH")"
install -d -o root -g "$SERVICE_GROUP" -m 2750 "$SNAPSHOT_DIR"
chown root:"$SERVICE_GROUP" "$SNAPSHOT_DIR"
chmod 2750 "$SNAPSHOT_DIR"
[[ -f "$SNAPSHOT_PATH" && -r "$SNAPSHOT_PATH" ]] \
    || { echo "Vibe 用户不可读 AQSP 快照: ${SNAPSHOT_PATH}" >&2; exit 1; }

if ! runuser -u "$SERVICE_USER" -- test -r "$SNAPSHOT_PATH" || {
    [[ -f "$SNAPSHOT_INDEX_PATH" ]] &&
    ! runuser -u "$SERVICE_USER" -- test -r "$SNAPSHOT_INDEX_PATH"
}; then
    if ! command -v setfacl >/dev/null 2>&1; then
        echo "隔离用户无法读取 600 快照，且系统没有 setfacl: ${SNAPSHOT_PATH}" >&2
        echo "请安装 acl 后重试；需要 u:${SERVICE_USER}:r-- 文件 ACL 和父目录 --x ACL。" >&2
        exit 1
    fi
    snapshot_dir="$(dirname "$SNAPSHOT_PATH")"
    while [[ "$snapshot_dir" != "/" && "$snapshot_dir" != "." ]]; do
        setfacl -m "u:${SERVICE_USER}:--x" "$snapshot_dir"
        snapshot_dir="$(dirname "$snapshot_dir")"
    done
    setfacl -m "d:u:${SERVICE_USER}:r--,d:m:r--" "$(dirname "$SNAPSHOT_PATH")"
    setfacl -m "u:${SERVICE_USER}:r--,m:r--" "$SNAPSHOT_PATH"
    if [[ -f "$SNAPSHOT_INDEX_PATH" ]]; then
        setfacl -m "u:${SERVICE_USER}:r--,m:r--" "$SNAPSHOT_INDEX_PATH"
    fi
    runuser -u "$SERVICE_USER" -- test -r "$SNAPSHOT_PATH" &&
        { [[ ! -f "$SNAPSHOT_INDEX_PATH" ]] ||
          runuser -u "$SERVICE_USER" -- test -r "$SNAPSHOT_INDEX_PATH"; } \
        || {
            echo "已尝试 ACL，但隔离用户仍无法读取快照或日期索引: ${SNAPSHOT_PATH}" >&2
            echo "请检查: getfacl ${SNAPSHOT_PATH}" >&2
            exit 1
        }
    echo "已授予 ${SERVICE_USER} 对快照的最小读取 ACL（文件 r--，父目录 --x）。"
fi

if [[ -z "$NPM_BIN" ]]; then
    NPM_BIN="$(command -v npm || true)"
fi
[[ -x "$NPM_BIN" ]] || { echo "缺少 npm，可用 --npm 指定绝对路径。" >&2; exit 1; }
[[ -x "$BOOTSTRAP_PYTHON" || "$BOOTSTRAP_PYTHON" == */* ]] \
    || BOOTSTRAP_PYTHON="$(command -v "$BOOTSTRAP_PYTHON" || true)"
[[ -x "$BOOTSTRAP_PYTHON" ]] || { echo "缺少 Python: ${BOOTSTRAP_PYTHON}" >&2; exit 1; }

"$BOOTSTRAP_PYTHON" -m venv "$VENV_DIR"
"${VENV_DIR}/bin/python" -m pip install -e "${PROJECT_ROOT}[api]"
"${VENV_DIR}/bin/python" -c 'import fastapi, uvicorn'
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$VENV_DIR"

if [[ "$SKIP_BUILD" != "true" ]]; then
    "$NPM_BIN" ci --prefix "${PROJECT_ROOT}/frontend"
    "$NPM_BIN" run build --prefix "${PROJECT_ROOT}/frontend"
fi
[[ -f "${PROJECT_ROOT}/frontend/dist/index.html" ]] \
    || { echo "缺少 frontend/dist/index.html，请移除 --skip-build 重试。" >&2; exit 1; }

# npm ci/build usually runs as root during provisioning, while Vite preview
# runs as the isolated service user and needs a writable config cache.
if [[ -d "${PROJECT_ROOT}/frontend/node_modules" ]]; then
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "${PROJECT_ROOT}/frontend/node_modules"
fi
if [[ -d "${PROJECT_ROOT}/frontend/dist" ]]; then
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "${PROJECT_ROOT}/frontend/dist"
fi

runuser -u "$SERVICE_USER" -- env \
    PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}/backend" \
    "${VENV_DIR}/bin/python" -c 'import aqsp, aqsp_bridge, fastapi, uvicorn'

escape_sed() {
    printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}
PROJECT_ROOT_ESCAPED="$(escape_sed "$PROJECT_ROOT")"
SERVICE_USER_ESCAPED="$(escape_sed "$SERVICE_USER")"
SERVICE_GROUP_ESCAPED="$(escape_sed "$SERVICE_GROUP")"
VENV_DIR_ESCAPED="$(escape_sed "$VENV_DIR")"
ENV_FILE_ESCAPED="$(escape_sed "$ENV_FILE")"
LOG_DIR_ESCAPED="$(escape_sed "$LOG_DIR")"
NPM_BIN_ESCAPED="$(escape_sed "$NPM_BIN")"

render_unit() {
    local source="$1"
    local destination="$2"
    sed \
        -e "s|@AQSP_PROJECT_ROOT@|${PROJECT_ROOT_ESCAPED}|g" \
        -e "s|@AQSP_VIBE_USER@|${SERVICE_USER_ESCAPED}|g" \
        -e "s|@AQSP_VIBE_GROUP@|${SERVICE_GROUP_ESCAPED}|g" \
        -e "s|@AQSP_VENV_DIR@|${VENV_DIR_ESCAPED}|g" \
        -e "s|@AQSP_ENV_FILE@|${ENV_FILE_ESCAPED}|g" \
        -e "s|@AQSP_LOG_DIR@|${LOG_DIR_ESCAPED}|g" \
        -e "s|@AQSP_NPM_BIN@|${NPM_BIN_ESCAPED}|g" \
        "$source" >"$destination"
    # Template comments document the placeholder syntax; only executable unit
    # lines must be checked for unresolved substitutions.
    ! grep -vE '^[[:space:]]*#' "$destination" | grep -Eq '@AQSP_[A-Z_]+@'
}

install -d "$SYSTEMD_DEST_DIR"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
render_unit "${SYSTEMD_SOURCE_DIR}/aqsp-vibe-research-api.service" "${tmp_dir}/aqsp-vibe-research-api.service"
render_unit "${SYSTEMD_SOURCE_DIR}/aqsp-vibe-research-preview.service" "${tmp_dir}/aqsp-vibe-research-preview.service"
install -m 0644 "${tmp_dir}/aqsp-vibe-research-api.service" "$SYSTEMD_DEST_DIR/"
install -m 0644 "${tmp_dir}/aqsp-vibe-research-preview.service" "$SYSTEMD_DEST_DIR/"
install -m 0644 "${SYSTEMD_SOURCE_DIR}/aqsp-vibe-research.target" "$SYSTEMD_DEST_DIR/"

"$SYSTEMCTL_BIN" daemon-reload
if [[ "$NO_START" == "true" ]]; then
    echo "Vibe-Research provisioned; services were not started (--no-start)."
else
    VIBE_RESEARCH_PYTHON_BIN="${VENV_DIR}/bin/python" \
    VIBE_RESEARCH_NPM_BIN="$NPM_BIN" \
        "${PROJECT_ROOT}/scripts/start_vibe_research_service.sh" --env-file "$ENV_FILE"
fi
