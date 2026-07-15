#!/usr/bin/env bash
# Start the AQSP rehearsal without touching an already healthy service.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${PROJECT_ROOT}/frontend"
BACKEND_DIR="${PROJECT_ROOT}/backend"
FRONTEND_HOST="${VIBE_RESEARCH_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${VIBE_RESEARCH_FRONTEND_PORT:-5899}"
BACKEND_HOST="${VIBE_RESEARCH_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${VIBE_RESEARCH_BACKEND_PORT:-8900}"
SNAPSHOT_PATH="${AQSP_RESEARCH_SURFACE_SNAPSHOT:-data/runtime/home_dashboard_snapshot.json}"
LOG_DIR="${VIBE_RESEARCH_LOG_DIR:-${PROJECT_ROOT}/logs/vibe-research}"
SKIP_BUILD="false"
CHECK_ONLY="false"

for bind_host in "$FRONTEND_HOST" "$BACKEND_HOST"; do
    case "$bind_host" in
        127.0.0.1|localhost|::1) ;;
        *)
            if [[ "${VIBE_RESEARCH_ALLOW_NONLOCAL_BIND:-false}" != "true" ]]; then
                echo "拒绝非本机监听地址 ${bind_host}；演练默认不暴露公网。" >&2
                echo "如确有受控内网需求，显式设置 VIBE_RESEARCH_ALLOW_NONLOCAL_BIND=true。" >&2
                exit 1
            fi
            ;;
    esac
done

usage() {
    cat <<'EOF'
用法: scripts/start_vibe_research.sh [--skip-build] [--check-only]

默认端口：前端 127.0.0.1:5899，FastAPI 127.0.0.1:8900。
已有健康进程会复用；端口被其他服务占用时直接失败，不会 kill 或覆盖进程。
EOF
}

while (($# > 0)); do
    case "$1" in
        --skip-build) SKIP_BUILD="true" ;;
        --check-only) CHECK_ONLY="true" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" && -x "${BACKEND_DIR}/.venv/bin/python" ]]; then
    PYTHON_BIN="${BACKEND_DIR}/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"

frontend_url="http://${FRONTEND_HOST}:${FRONTEND_PORT}"
backend_url="http://${BACKEND_HOST}:${BACKEND_PORT}"

is_http_healthy() {
    local url="$1"
    curl --silent --show-error --fail --max-time 3 "$url" >/dev/null 2>&1
}

is_tcp_open() {
    "$PYTHON_BIN" - "$1" "$2" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
try:
    with socket.create_connection((probe_host, port), timeout=0.4):
        raise SystemExit(0)
except OSError:
    raise SystemExit(1)
PY
}

assert_port_available_or_expected() {
    local label="$1"
    local host="$2"
    local port="$3"
    local health_url="$4"
    local expected_text="$5"

    if is_http_healthy "$health_url" &&
        [[ -z "$expected_text" || "$(curl --silent --show-error --max-time 3 "$health_url")" == *"$expected_text"* ]]; then
        echo "${label}: reuse healthy service at ${host}:${port}"
        return 0
    fi
    if is_tcp_open "$host" "$port"; then
        echo "${label}: port ${host}:${port} is occupied but health check failed; refusing to replace it" >&2
        exit 1
    fi
    return 1
}

wait_for_http() {
    local label="$1"
    local url="$2"
    local attempts="${3:-40}"
    local i
    for ((i = 1; i <= attempts; i++)); do
        if is_http_healthy "$url"; then
            echo "${label}: ready (${url})"
            return 0
        fi
        sleep 0.25
    done
    echo "${label}: timed out waiting for ${url}" >&2
    return 1
}

if [[ "$CHECK_ONLY" == "true" ]]; then
    exec "${PROJECT_ROOT}/scripts/check_vibe_research.sh" \
        --frontend-url "$frontend_url" \
        --backend-url "$backend_url" \
        --snapshot "$SNAPSHOT_PATH"
fi

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    echo "缺少 frontend/node_modules；先执行: cd frontend && npm ci" >&2
    exit 1
fi

if [[ "$SKIP_BUILD" != "true" ]]; then
    echo "构建 Vite 产物: frontend/dist"
    (cd "$FRONTEND_DIR" && npm run build)
fi

if [[ ! -f "$FRONTEND_DIR/dist/index.html" ]]; then
    echo "缺少 Vite 构建产物: ${FRONTEND_DIR}/dist/index.html" >&2
    exit 1
fi

started_backend="false"
started_frontend="false"
backend_pid=""
frontend_pid=""

cleanup() {
    local status=$?
    if [[ "$started_frontend" == "true" && -n "$frontend_pid" ]]; then
        kill "$frontend_pid" 2>/dev/null || true
        wait "$frontend_pid" 2>/dev/null || true
    fi
    if [[ "$started_backend" == "true" && -n "$backend_pid" ]]; then
        kill "$backend_pid" 2>/dev/null || true
        wait "$backend_pid" 2>/dev/null || true
    fi
    exit "$status"
}
trap cleanup EXIT INT TERM

if ! assert_port_available_or_expected "FastAPI" "$BACKEND_HOST" "$BACKEND_PORT" "${backend_url}/api/health" "aqsp-api"; then
    mkdir -p "$LOG_DIR"
    echo "启动 FastAPI: ${backend_url}"
    (
        cd "$BACKEND_DIR"
        exec env AQSP_RESEARCH_SURFACE_SNAPSHOT="$SNAPSHOT_PATH" \
            "$PYTHON_BIN" -m uvicorn app:app \
            --host "$BACKEND_HOST" \
            --port "$BACKEND_PORT" \
            >"${LOG_DIR}/backend.log" 2>&1
    ) &
    backend_pid=$!
    started_backend="true"
    wait_for_http "FastAPI" "${backend_url}/api/health"
fi

if ! assert_port_available_or_expected "Vite preview" "$FRONTEND_HOST" "$FRONTEND_PORT" "${frontend_url}/" "AQSP"; then
    mkdir -p "$LOG_DIR"
    echo "启动 Vite preview: ${frontend_url}"
    (
        cd "$FRONTEND_DIR"
        exec env VITE_API_URL="${backend_url}" npm run preview -- \
            --host "$FRONTEND_HOST" \
            --port "$FRONTEND_PORT" \
            --strictPort
    ) >"${LOG_DIR}/frontend.log" 2>&1 &
    frontend_pid=$!
    started_frontend="true"
    wait_for_http "Vite preview" "${frontend_url}/"
fi

AQSP_RESEARCH_SURFACE_SNAPSHOT="$SNAPSHOT_PATH" \
    "${PROJECT_ROOT}/scripts/check_vibe_research.sh" \
    --frontend-url "$frontend_url" \
    --backend-url "$backend_url" \
    --snapshot "$SNAPSHOT_PATH"

if [[ "$started_backend" == "false" && "$started_frontend" == "false" ]]; then
    echo "All AQSP services were already healthy; nothing was restarted."
    exit 0
fi

echo "AQSP rehearsal is running; press Ctrl-C to stop only processes started by this script."
wait
