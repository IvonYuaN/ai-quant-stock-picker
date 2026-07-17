#!/usr/bin/env bash
# 消息面雷达：
# 1. 周末/盘前/午盘复核高影响资讯催化和风险
# 2. 只输出结果、影响、来源和状态

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_PYTHON_HELPER="${PROJECT_ROOT}/scripts/runtime_python.sh"
if [ ! -f "$RUNTIME_PYTHON_HELPER" ] && [ -f "${SCRIPT_DIR}/runtime_python.sh" ]; then
    RUNTIME_PYTHON_HELPER="${SCRIPT_DIR}/runtime_python.sh"
fi
if [ ! -f "$RUNTIME_PYTHON_HELPER" ]; then
    echo "[ERROR] 缺少运行时 Python 解析器: ${RUNTIME_PYTHON_HELPER}" >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$RUNTIME_PYTHON_HELPER"
LOG_DIR="${PROJECT_ROOT}/logs/news"
RESULT_LOG="${LOG_DIR}/news-$(date +%Y-%m-%d).log"

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RESULT_LOG"
}

is_truthy() {
    local value
    value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$value" =~ ^(1|true|yes|on)$ ]]
}

write_failed_json() {
    local reason="$1"
    local source_status="${2:-failed}"
    mkdir -p "$(dirname "$JSON_OUTPUT")"
    NEWS_FAILURE_OUTPUT="$JSON_OUTPUT" \
    NEWS_FAILURE_REASON="$reason" \
    NEWS_FAILURE_SOURCE_STATUS="$source_status" \
    "$PYTHON_BIN" - <<'AQSP_NEWS_FAILURE_JSON'
import json
import os
from pathlib import Path

from aqsp.core.time import now_shanghai

path = Path(os.environ["NEWS_FAILURE_OUTPUT"])
generated_at = now_shanghai().isoformat(timespec="seconds")
payload = {
    "date": generated_at[:10],
    "generated_at": generated_at,
    "source_status": os.environ["NEWS_FAILURE_SOURCE_STATUS"],
    "event_status": "source_failed",
    "raw_news_count": 0,
    "stale_news_count": 0,
    "undated_news_count": 0,
    "events": [],
    "warnings": [os.environ["NEWS_FAILURE_REASON"]],
    "source_statuses": [],
}
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.replace(path)
AQSP_NEWS_FAILURE_JSON
}

write_failed_report() {
    local reason="$1"
    local report_dir
    local tmp_report
    report_dir="$(dirname "$OUTPUT")"
    mkdir -p "$report_dir"
    tmp_report="${OUTPUT}.tmp.$$"
    {
        echo "# 消息面雷达-$(date +%F)"
        echo
        echo "## 结论"
        echo
        echo "- 无有效结论：消息源失败"
        echo "- 数据状态: 失败"
        echo
        echo "## 事件"
        echo
        echo "- 无可靠消息面结果"
        echo
        echo "## 状态"
        echo
        echo "- 状态: failed"
        echo "- 高影响事件: 0"
        echo "- 原因: ${reason}"
        echo "- 告警: 有"
    } >"$tmp_report"
    mv -f "$tmp_report" "$OUTPUT"
}

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
    log "已加载 .env 配置"
fi

PYTHON_BIN="$(aqsp_runtime_python "$PROJECT_ROOT")"
aqsp_require_runtime_python "$PYTHON_BIN"

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
export TZ="${TZ:-Asia/Shanghai}"
export AQSP_RUN_TASK_ID="news"

SYMBOLS="${AQSP_NEWS_SYMBOLS:-}"
NAMES="${AQSP_NEWS_NAMES:-}"
MAX_EVENTS="${AQSP_NEWS_MAX_EVENTS:-8}"
MAX_NEWS_AGE_DAYS="${AQSP_NEWS_MAX_NEWS_AGE_DAYS:-7}"
SOURCE_TIMEOUT_SECONDS="${AQSP_NEWS_SOURCE_TIMEOUT_SECONDS:-8}"
LLM_TIMEOUT_SECONDS="${AQSP_NEWS_LLM_TIMEOUT_SECONDS:-8}"
MAX_LLM_REVIEW_EVENTS="${AQSP_NEWS_MAX_LLM_REVIEW_EVENTS:-1}"
TASK_TIMEOUT_SECONDS="${AQSP_NEWS_TASK_TIMEOUT_SECONDS:-300}"
OUTPUT="${AQSP_NEWS_OUTPUT:-reports/news_catalysts.md}"
JSON_OUTPUT="${AQSP_NEWS_JSON_OUTPUT:-data/runtime/news_catalysts_latest.json}"
ENABLE_LLM_REVIEW="${AQSP_NEWS_ENABLE_LLM_REVIEW:-false}"
ALLOW_NON_TRADING_NOTIFY="${AQSP_ALLOW_NON_TRADING_NEWS_NOTIFY:-false}"

has_usable_current_news() {
    # A transient source failure must not erase a valid same-day report that
    # was fetched minutes earlier. Historical or empty artifacts are not kept.
    [ -s "$JSON_OUTPUT" ] || return 1
    "$PYTHON_BIN" - "$JSON_OUTPUT" <<'AQSP_CURRENT_NEWS_CHECK'
import json
import sys
from datetime import datetime
from pathlib import Path

from aqsp.core.time import today_shanghai, to_shanghai

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, ValueError, IndexError):
    raise SystemExit(1)

if str(payload.get("date", "")).strip() != today_shanghai().isoformat():
    raise SystemExit(1)
source_status = str(payload.get("source_status", "")).strip()
if source_status not in {"ok", "partial"}:
    raise SystemExit(1)
events = payload.get("events")
if not isinstance(events, list):
    raise SystemExit(1)
current_count = 0
for event in events:
    if not isinstance(event, dict) or not str(event.get("title", "")).strip():
        raise SystemExit(1)
    published_at = str(event.get("published_at", "")).strip()
    try:
        published_date = to_shanghai(
            datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        ).date().isoformat()
    except ValueError:
        raise SystemExit(1)
    if published_date == today_shanghai().isoformat():
        current_count += 1
raw_count = int(payload.get("raw_news_count", 0) or 0)
event_status = str(payload.get("event_status", "")).strip()
if current_count == 0 and (event_status != "no_high_impact" or raw_count <= 0):
    raise SystemExit(1)
raise SystemExit(0)
AQSP_CURRENT_NEWS_CHECK
}

PREVIOUS_NEWS_JSON="${JSON_OUTPUT}.previous.$$"
PREVIOUS_NEWS_REPORT="${OUTPUT}.previous.$$"
cleanup_previous_news() {
    rm -f "$PREVIOUS_NEWS_JSON" "$PREVIOUS_NEWS_REPORT"
}
trap cleanup_previous_news EXIT

if has_usable_current_news; then
    cp -f "$JSON_OUTPUT" "$PREVIOUS_NEWS_JSON"
    if [ -f "$OUTPUT" ]; then
        cp -f "$OUTPUT" "$PREVIOUS_NEWS_REPORT"
    fi
fi

restore_previous_current_news() {
    [ -s "$PREVIOUS_NEWS_JSON" ] || return 1
    cp -f "$PREVIOUS_NEWS_JSON" "$JSON_OUTPUT"
    if [ -s "$PREVIOUS_NEWS_REPORT" ]; then
        cp -f "$PREVIOUS_NEWS_REPORT" "$OUTPUT"
    fi
    log "消息结果失败：恢复任务开始前的同日消息产物"
    return 0
}

LLM_ARGS=()
if is_truthy "$ENABLE_LLM_REVIEW"; then
    LLM_ARGS=(--enable-llm-review)
fi

NEWS_NOTIFY_ENABLED="${AQSP_NEWS_NOTIFY:-false}"
export AQSP_NOTIFY="false"
export AQSP_GATE_NOTIFY="false"
export AQSP_NEWS_SOURCE_TIMEOUT_SECONDS="$SOURCE_TIMEOUT_SECONDS"

NOTIFY_ARGS=()
if is_truthy "$NEWS_NOTIFY_ENABLED"; then
    if "${PYTHON_BIN}" - <<'AQSP_CALENDAR_PY'
from aqsp.core.time import is_trading_day, today_shanghai
raise SystemExit(0 if is_trading_day(today_shanghai()) else 1)
AQSP_CALENDAR_PY
    then
        NOTIFY_ARGS=(--notify)
    elif is_truthy "$ALLOW_NON_TRADING_NOTIFY"; then
        NOTIFY_ARGS=(--notify)
    else
        log "今日非交易日，消息面雷达仅写报告；设置 AQSP_ALLOW_NON_TRADING_NEWS_NOTIFY=true 才允许非交易日推送"
    fi
else
    log "消息面雷达默认不推送手机通知；设置 AQSP_NEWS_NOTIFY=true 才推送"
fi

log "开始消息面雷达"
cd "$PROJECT_ROOT"
NEWS_CMD=(
    "${PYTHON_BIN}" -m aqsp news-catalysts
    --symbols "$SYMBOLS" \
    --names "$NAMES" \
    --max-events "$MAX_EVENTS" \
    --max-news-age-days "$MAX_NEWS_AGE_DAYS" \
    --source-timeout-seconds "$SOURCE_TIMEOUT_SECONDS" \
    --llm-timeout-seconds "$LLM_TIMEOUT_SECONDS" \
    --max-llm-review-events "$MAX_LLM_REVIEW_EVENTS" \
    --output "$OUTPUT" \
    --json-output "$JSON_OUTPUT"
)
if [ "${#NOTIFY_ARGS[@]}" -gt 0 ]; then
    NEWS_CMD+=("${NOTIFY_ARGS[@]}")
fi
if [ "${#LLM_ARGS[@]}" -gt 0 ]; then
    NEWS_CMD+=("${LLM_ARGS[@]}")
fi

set +e
if command -v timeout >/dev/null 2>&1; then
    timeout "${TASK_TIMEOUT_SECONDS}" "${NEWS_CMD[@]}" 2>&1 | tee -a "$RESULT_LOG"
    NEWS_EXIT=${PIPESTATUS[0]}
else
    "${NEWS_CMD[@]}" 2>&1 | tee -a "$RESULT_LOG"
    NEWS_EXIT=${PIPESTATUS[0]}
fi
set -e

if [ "$NEWS_EXIT" -eq 124 ]; then
    if restore_previous_current_news; then
        :
    elif has_usable_current_news; then
        log "消息源超时：保留已有同日消息产物，不覆盖有效证据"
    elif ! write_failed_report "消息源超时"; then
        log "[WARN] 超时失败报告写入失败: ${OUTPUT}"
    fi
    if has_usable_current_news; then
        :
    elif ! write_failed_json "消息源超时" "timeout"; then
        log "[WARN] 超时失败 JSON 写入失败: ${JSON_OUTPUT}"
    fi
    log "消息面雷达超时: ${OUTPUT}"
elif [ "$NEWS_EXIT" -ne 0 ]; then
    if restore_previous_current_news; then
        :
    elif has_usable_current_news; then
        log "消息面命令失败：保留已有同日消息产物，不覆盖有效证据"
    elif ! write_failed_report "消息面雷达命令失败: exit=${NEWS_EXIT}"; then
        log "[WARN] 失败报告写入失败: ${OUTPUT}"
    fi
    if has_usable_current_news; then
        :
    elif ! write_failed_json "消息面雷达命令失败: exit=${NEWS_EXIT}" "failed"; then
        log "[WARN] 失败 JSON 写入失败: ${JSON_OUTPUT}"
    fi
    log "[ERROR] 消息面雷达失败: exit=${NEWS_EXIT}"
    exit "$NEWS_EXIT"
elif ! has_usable_current_news && restore_previous_current_news; then
    log "消息面任务返回成功但产物状态失败：已恢复同日消息"
fi
log "消息面雷达完成: ${OUTPUT}"

find "$LOG_DIR" -name "news-*.log" -mtime +30 -delete 2>/dev/null || true
