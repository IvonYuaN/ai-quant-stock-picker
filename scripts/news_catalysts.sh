#!/usr/bin/env bash
# 消息面雷达：
# 1. 周末/盘前/午盘复核高影响资讯催化和风险
# 2. 只输出结果、影响、来源和状态

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python3"
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

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
    log "已加载 .env 配置"
fi

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
export TZ="${TZ:-Asia/Shanghai}"
export AQSP_RUN_TASK_ID="news"
export AQSP_NOTIFY="false"

SYMBOLS="${AQSP_NEWS_SYMBOLS:-}"
NAMES="${AQSP_NEWS_NAMES:-}"
MAX_EVENTS="${AQSP_NEWS_MAX_EVENTS:-8}"
SOURCE_TIMEOUT_SECONDS="${AQSP_NEWS_SOURCE_TIMEOUT_SECONDS:-4}"
LLM_TIMEOUT_SECONDS="${AQSP_NEWS_LLM_TIMEOUT_SECONDS:-8}"
MAX_LLM_REVIEW_EVENTS="${AQSP_NEWS_MAX_LLM_REVIEW_EVENTS:-1}"
TASK_TIMEOUT_SECONDS="${AQSP_NEWS_TASK_TIMEOUT_SECONDS:-300}"
OUTPUT="${AQSP_NEWS_OUTPUT:-reports/news_catalysts.md}"
ENABLE_LLM_REVIEW="${AQSP_NEWS_ENABLE_LLM_REVIEW:-false}"

LLM_ARGS=()
if is_truthy "$ENABLE_LLM_REVIEW"; then
    LLM_ARGS=(--enable-llm-review)
fi

NOTIFY_ARGS=()
if is_truthy "${AQSP_NEWS_NOTIFY:-false}"; then
    NOTIFY_ARGS=(--notify)
else
    log "消息面雷达默认不推送手机通知，仅写报告；设置 AQSP_NEWS_NOTIFY=true 才推送"
fi

log "开始消息面雷达"
cd "$PROJECT_ROOT"
NEWS_CMD=(
    "${PYTHON_BIN}" -m aqsp news-catalysts
    --symbols "$SYMBOLS" \
    --names "$NAMES" \
    --max-events "$MAX_EVENTS" \
    --source-timeout-seconds "$SOURCE_TIMEOUT_SECONDS" \
    --llm-timeout-seconds "$LLM_TIMEOUT_SECONDS" \
    --max-llm-review-events "$MAX_LLM_REVIEW_EVENTS" \
    --output "$OUTPUT" \
    "${NOTIFY_ARGS[@]}" \
    "${LLM_ARGS[@]}"
)

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
    mkdir -p "$(dirname "$OUTPUT")"
    {
        echo "# 消息面雷达-$(date +%F)"
        echo
        echo "## 结论"
        echo
        echo "- 无有效结论：消息源超时"
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
        echo "- 告警: 有"
    } > "$OUTPUT"
    log "消息面雷达超时: ${OUTPUT}"
elif [ "$NEWS_EXIT" -ne 0 ]; then
    log "[ERROR] 消息面雷达失败: exit=${NEWS_EXIT}"
    exit "$NEWS_EXIT"
fi
log "消息面雷达完成: ${OUTPUT}"

find "$LOG_DIR" -name "news-*.log" -mtime +30 -delete 2>/dev/null || true
