#!/usr/bin/env bash
# Health and preflight checks for the persistent AQSP services.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPONENT="all"
ENV_FILE=""
PREFLIGHT_ONLY="false"
PORT_GUARD="false"
SKIP_SNAPSHOT="false"
SYSTEMD_UNIT="${AQSP_VIBE_SYSTEMD_TARGET:-aqsp-vibe-research.target}"
API_URL=""
FRONTEND_URL=""

usage() {
    cat <<'EOF'
用法: scripts/health_vibe_research.sh [选项]

  --env-file PATH       读取 systemd EnvironmentFile（不会打印变量）
  --component NAME      api、frontend 或 all，默认 all
  --preflight-only      只检查依赖、快照和构建产物，不请求 HTTP
  --port-guard          预检时拒绝已被占用的 API/前端端口
  --skip-snapshot       健康检查时不要求 AQSP 快照
  --api-url URL         API 地址，默认 http://127.0.0.1:8900
  --frontend-url URL    前端地址，默认 http://127.0.0.1:5899
  --systemd-unit NAME   同时检查 systemd 单元状态
EOF
}

while (($# > 0)); do
    case "$1" in
        --env-file) ENV_FILE="${2:?缺少 --env-file 参数}"; shift ;;
        --component) COMPONENT="${2:?缺少 --component 参数}"; shift ;;
        --preflight-only) PREFLIGHT_ONLY="true" ;;
        --port-guard) PORT_GUARD="true" ;;
        --skip-snapshot) SKIP_SNAPSHOT="true" ;;
        --api-url) API_URL="${2:?缺少 --api-url 参数}"; shift ;;
        --frontend-url) FRONTEND_URL="${2:?缺少 --frontend-url 参数}"; shift ;;
        --systemd-unit) SYSTEMD_UNIT="${2:?缺少 --systemd-unit 参数}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

case "$COMPONENT" in
    api|frontend|all) ;;
    *) echo "--component 只能是 api、frontend 或 all" >&2; exit 2 ;;
esac

if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -r "$ENV_FILE" ]]; then
        echo "环境文件不可读: ${ENV_FILE}" >&2
        exit 1
    fi
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

PYTHON_BIN="${VIBE_RESEARCH_PYTHON_BIN:-${PYTHON_BIN:-}}"
if [[ -z "$PYTHON_BIN" && -x "${PROJECT_ROOT}/.venv/bin/python3" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python3"
elif [[ -z "$PYTHON_BIN" && -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
elif [[ -z "$PYTHON_BIN" && -x "${PROJECT_ROOT}/backend/venv/bin/python3" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/backend/venv/bin/python3"
elif [[ -z "$PYTHON_BIN" && -x "${PROJECT_ROOT}/backend/venv/bin/python" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/backend/venv/bin/python"
elif [[ -z "$PYTHON_BIN" && -x "${PROJECT_ROOT}/backend/.venv/bin/python" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/backend/.venv/bin/python"
elif [[ -z "$PYTHON_BIN" && -x "${PROJECT_ROOT}/backend/.venv/bin/python3" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/backend/.venv/bin/python3"
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"
FRONTEND_HOST="${VIBE_RESEARCH_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${VIBE_RESEARCH_FRONTEND_PORT:-5899}"
BACKEND_HOST="${VIBE_RESEARCH_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${VIBE_RESEARCH_BACKEND_PORT:-8900}"
SNAPSHOT_PATH="${AQSP_RESEARCH_SURFACE_SNAPSHOT:-}"
API_URL="${API_URL:-http://${BACKEND_HOST}:${BACKEND_PORT}}"
FRONTEND_URL="${FRONTEND_URL:-http://${FRONTEND_HOST}:${FRONTEND_PORT}}"

resolve_path() {
    local raw="$1"
    if [[ "$raw" = /* ]]; then
        printf '%s\n' "$raw"
    else
        printf '%s\n' "${PROJECT_ROOT}/${raw}"
    fi
}

fail() {
    echo "FAIL $1" >&2
    exit 1
}

check_snapshot() {
    [[ -n "$SNAPSHOT_PATH" ]] || fail "AQSP_RESEARCH_SURFACE_SNAPSHOT 未设置"
    local snapshot_file
    snapshot_file="$(resolve_path "$SNAPSHOT_PATH")"
    [[ -f "$snapshot_file" && -r "$snapshot_file" ]] || fail "AQSP 快照不可读: ${snapshot_file}"
    "$PYTHON_BIN" - "$snapshot_file" <<'PY'
from datetime import date
import json
import math
import sys
import time
from datetime import datetime


def fail(message: str) -> None:
    raise ValueError(message)


def mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        fail(f"{label} 必须是对象")
    return value


def required(value: dict[str, object], keys: set[str], label: str) -> None:
    missing = sorted(keys - value.keys())
    if missing:
        fail(f"{label} 缺少字段: {', '.join(missing)}")


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} 必须是非空字符串")
    return value.strip()


def text_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        fail(f"{label} 必须是字符串数组")
    return value


def timestamp(value: object, label: str) -> float:
    raw = text(value, label)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} 不是 ISO 8601 时间戳") from exc
    if parsed.tzinfo is None:
        fail(f"{label} 必须包含时区")
    return parsed.timestamp()

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = mapping(json.load(handle), "snapshot")

required(
    payload,
    {
        "schema_version",
        "generated_at",
        "stale_after",
        "selected_date",
        "available_dates",
        "candidates",
        "debates",
        "summaries",
        "source",
        "coldstart",
        "messages",
    },
    "snapshot",
)
if text(payload["schema_version"], "schema_version") != "v1":
    fail("不支持的 snapshot schema_version")
generated_at = timestamp(payload["generated_at"], "generated_at")
stale_after = timestamp(payload["stale_after"], "stale_after")
if stale_after < generated_at:
    fail("stale_after 不得早于 generated_at")
if stale_after <= time.time():
    fail("snapshot 已过期")

selected_date = text(payload["selected_date"], "selected_date")
try:
    date.fromisoformat(selected_date)
except ValueError as exc:
    raise ValueError("selected_date 必须是 YYYY-MM-DD") from exc
available_dates = text_list(payload["available_dates"], "available_dates")
if selected_date not in available_dates:
    fail("selected_date 必须存在于 available_dates")

candidates = payload["candidates"]
if not isinstance(candidates, list):
    fail("candidates 必须是数组")
candidate_symbols: set[str] = set()
for index, candidate in enumerate(candidates):
    item = mapping(candidate, f"candidate[{index}]")
    required(item, {"symbol", "display_name", "score", "research_status", "next_step", "context"}, f"candidate[{index}]")
    symbol = text(item["symbol"], f"candidate[{index}].symbol")
    if symbol in candidate_symbols:
        fail(f"candidates 存在重复 symbol: {symbol}")
    candidate_symbols.add(symbol)
    if not isinstance(item["score"], (int, float)) or isinstance(item["score"], bool) or not math.isfinite(item["score"]):
        fail(f"candidate[{index}].score 必须是有限数字")
    for key in ("display_name", "research_status", "next_step", "context"):
        text(item[key], f"candidate[{index}].{key}")

debates = payload["debates"]
if not isinstance(debates, list):
    fail("debates 必须是数组")
debate_symbols: set[str] = set()
for index, debate in enumerate(debates):
    item = mapping(debate, f"debate[{index}]")
    required(item, {"symbol", "display_name", "conclusion", "primary_risk_gate", "next_trigger", "active_roles"}, f"debate[{index}]")
    symbol = text(item["symbol"], f"debate[{index}].symbol")
    if symbol in debate_symbols:
        fail(f"debates 存在重复 symbol: {symbol}")
    if symbol not in candidate_symbols:
        fail(f"debate[{index}].symbol 未映射到 candidates: {symbol}")
    debate_symbols.add(symbol)
    for key in ("display_name", "conclusion", "primary_risk_gate", "next_trigger"):
        text(item[key], f"debate[{index}].{key}")
    text_list(item["active_roles"], f"debate[{index}].active_roles")

messages = payload["messages"]
if not isinstance(messages, list):
    fail("messages 必须是数组")
for index, message in enumerate(messages):
    item = mapping(message, f"message[{index}]")
    required(item, {"title", "summary", "impact", "category", "source", "published_at"}, f"message[{index}]")
    for key in ("title", "summary", "impact", "category", "source"):
        text(item[key], f"message[{index}].{key}")
    timestamp(item["published_at"], f"message[{index}].published_at")
    text_list(item.get("affected_symbols", []), f"message[{index}].affected_symbols")
PY
    echo "PASS AQSP snapshot schema/mapping: ${snapshot_file}"
}

check_port_free() {
    local host="$1"
    local port="$2"
    "$PYTHON_BIN" - "$host" "$port" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
try:
    with socket.create_connection((probe_host, port), timeout=0.4):
        raise SystemExit(1)
except OSError:
    raise SystemExit(0)
PY
}

check_api_preflight() {
    [[ -f "${PROJECT_ROOT}/backend/app.py" ]] || fail "缺少 backend/app.py"
    "$PYTHON_BIN" -c "import fastapi, uvicorn" \
        || fail "Python API 依赖缺失，请检查 ${PYTHON_BIN}"
    check_snapshot
    if [[ "$PORT_GUARD" == "true" ]]; then
        check_port_free "$BACKEND_HOST" "$BACKEND_PORT" \
            || fail "API 端口已被占用: ${BACKEND_HOST}:${BACKEND_PORT}"
    fi
    echo "PASS API dependencies"
}

check_frontend_preflight() {
    [[ -d "${PROJECT_ROOT}/frontend/node_modules" ]] \
        || fail "缺少 frontend/node_modules，请先执行 npm ci"
    [[ -f "${PROJECT_ROOT}/frontend/dist/index.html" ]] \
        || fail "缺少 frontend/dist/index.html，请先执行 npm run build"
    NPM_BIN="${VIBE_RESEARCH_NPM_BIN:-$(command -v npm || true)}"
    [[ -x "$NPM_BIN" ]] || fail "缺少 npm 运行时"
    if [[ "$PORT_GUARD" == "true" ]]; then
        check_port_free "$FRONTEND_HOST" "$FRONTEND_PORT" \
            || fail "前端端口已被占用: ${FRONTEND_HOST}:${FRONTEND_PORT}"
    fi
    echo "PASS frontend dependencies"
}

check_live() {
    local api_body frontend_body dates_body
    if [[ "$COMPONENT" == "api" || "$COMPONENT" == "all" ]]; then
        api_body="$(curl --silent --show-error --fail --max-time 8 "${API_URL%/}/api/health")" \
            || fail "API 健康检查失败: ${API_URL}"
        [[ "$api_body" == *'"ok":true'* && "$api_body" == *'aqsp-api'* ]] \
            || fail "API 健康响应不符合契约: ${api_body}"
        echo "PASS API health: ${API_URL}"

        if [[ "$SKIP_SNAPSHOT" != "true" ]]; then
            dates_body="$(curl --silent --show-error --fail --max-time 8 "${API_URL%/}/api/aqsp/dates")" \
                || fail "AQSP 日期接口失败"
            [[ "$dates_body" == *'"selected_date"'* ]] \
                || fail "AQSP 日期响应缺少 selected_date"
            echo "PASS AQSP dates"
        else
            echo "SKIP AQSP dates"
        fi
    fi

    if [[ "$COMPONENT" == "frontend" || "$COMPONENT" == "all" ]]; then
        frontend_body="$(curl --silent --show-error --fail --max-time 8 "${FRONTEND_URL%/}/")" \
            || fail "Vite preview 健康检查失败: ${FRONTEND_URL}"
        [[ "$frontend_body" == *"AQSP"* ]] \
            || fail "前端首页不是 AQSP 构建产物"
        echo "PASS frontend health: ${FRONTEND_URL}"
    fi
}

if [[ "$COMPONENT" == "api" || "$COMPONENT" == "all" ]]; then
    check_api_preflight
fi
if [[ "$COMPONENT" == "frontend" || "$COMPONENT" == "all" ]]; then
    check_frontend_preflight
fi

if [[ "$PREFLIGHT_ONLY" == "true" ]]; then
    echo "AQSP preflight passed."
    exit 0
fi

command -v curl >/dev/null 2>&1 || fail "缺少 curl"
if [[ -n "$SYSTEMD_UNIT" ]]; then
    SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
    "$SYSTEMCTL_BIN" is-active --quiet "$SYSTEMD_UNIT" \
        || fail "systemd 单元未运行: ${SYSTEMD_UNIT}"
    echo "PASS systemd: ${SYSTEMD_UNIT}"
fi
check_live
echo "AQSP health check passed."
