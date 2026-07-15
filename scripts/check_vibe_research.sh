#!/usr/bin/env bash
# Verify the local AQSP frontend/API deployment chain.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_URL="http://127.0.0.1:5899"
BACKEND_URL="http://127.0.0.1:8900"
SNAPSHOT_PATH="${AQSP_RESEARCH_SURFACE_SNAPSHOT:-data/runtime/home_dashboard_snapshot.json}"
REQUIRE_SNAPSHOT="true"

usage() {
    cat <<'EOF'
用法: scripts/check_vibe_research.sh [选项]

  --frontend-url URL  前端 Vite preview 地址，默认 http://127.0.0.1:5899
  --backend-url URL   FastAPI 地址，默认 http://127.0.0.1:8900
  --snapshot PATH     AQSP_RESEARCH_SURFACE_SNAPSHOT 路径
  --skip-snapshot     仅检查页面和 API，不要求快照文件/日期接口成功
EOF
}

while (($# > 0)); do
    case "$1" in
        --frontend-url) FRONTEND_URL="${2:?缺少 --frontend-url 参数}"; shift ;;
        --backend-url) BACKEND_URL="${2:?缺少 --backend-url 参数}"; shift ;;
        --snapshot) SNAPSHOT_PATH="${2:?缺少 --snapshot 参数}"; shift ;;
        --skip-snapshot) REQUIRE_SNAPSHOT="false" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

resolve_path() {
    local raw="$1"
    if [[ "$raw" = /* ]]; then
        printf '%s\n' "$raw"
    else
        printf '%s\n' "${PROJECT_ROOT}/${raw}"
    fi
}

fetch() {
    curl --silent --show-error --fail --max-time 8 "$1"
}

assert_contains() {
    local label="$1"
    local value="$2"
    local expected="$3"
    if [[ "$value" != *"$expected"* ]]; then
        echo "FAIL ${label}: missing ${expected}" >&2
        exit 1
    fi
    echo "PASS ${label}"
}

frontend_body="$(fetch "${FRONTEND_URL%/}/")"
assert_contains "frontend root" "$frontend_body" "AQSP"

backend_health="$(fetch "${BACKEND_URL%/}/api/health")"
assert_contains "backend health" "$backend_health" '"ok":true'
assert_contains "backend service" "$backend_health" 'aqsp-api'

frontend_api_health="$(fetch "${FRONTEND_URL%/}/api/health")"
assert_contains "frontend /api proxy" "$frontend_api_health" '"ok":true'

if [[ "$REQUIRE_SNAPSHOT" == "true" ]]; then
    snapshot_file="$(resolve_path "$SNAPSHOT_PATH")"
    if [[ ! -f "$snapshot_file" ]]; then
        echo "FAIL AQSP snapshot: file not found: ${snapshot_file}" >&2
        exit 1
    fi
    dates_body="$(fetch "${BACKEND_URL%/}/api/aqsp/dates")"
    assert_contains "AQSP dates" "$dates_body" '"selected_date"'
    echo "PASS AQSP snapshot: ${snapshot_file}"
else
    echo "SKIP AQSP snapshot"
fi

echo "AQSP local health check passed."
