#!/usr/bin/env bash
# 消息面雷达：
# 1. 周末/盘前/午盘复核高影响资讯催化和风险
# 2. 只生成研究提示，不改评分、不写正式 ledger

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

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
    log "已加载 .env 配置"
fi

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
export TZ="${TZ:-Asia/Shanghai}"

SYMBOLS="${AQSP_NEWS_SYMBOLS:-}"
NAMES="${AQSP_NEWS_NAMES:-}"
MAX_EVENTS="${AQSP_NEWS_MAX_EVENTS:-8}"
SOURCE_TIMEOUT_SECONDS="${AQSP_NEWS_SOURCE_TIMEOUT_SECONDS:-8}"
LLM_TIMEOUT_SECONDS="${AQSP_NEWS_LLM_TIMEOUT_SECONDS:-8}"
MAX_LLM_REVIEW_EVENTS="${AQSP_NEWS_MAX_LLM_REVIEW_EVENTS:-3}"
OUTPUT="${AQSP_NEWS_OUTPUT:-reports/news_catalysts.md}"
ENABLE_LLM_REVIEW="${AQSP_NEWS_ENABLE_LLM_REVIEW:-false}"

LLM_ARGS=()
if [[ "${ENABLE_LLM_REVIEW,,}" =~ ^(1|true|yes|on)$ ]]; then
    LLM_ARGS=(--enable-llm-review)
fi

log "开始消息面雷达"
cd "$PROJECT_ROOT"
"${PYTHON_BIN}" -m aqsp news-catalysts \
    --symbols "$SYMBOLS" \
    --names "$NAMES" \
    --max-events "$MAX_EVENTS" \
    --source-timeout-seconds "$SOURCE_TIMEOUT_SECONDS" \
    --llm-timeout-seconds "$LLM_TIMEOUT_SECONDS" \
    --max-llm-review-events "$MAX_LLM_REVIEW_EVENTS" \
    --output "$OUTPUT" \
    --notify \
    "${LLM_ARGS[@]}" 2>&1 | tee -a "$RESULT_LOG"
log "消息面雷达完成: ${OUTPUT}"

find "$LOG_DIR" -name "news-*.log" -mtime +30 -delete 2>/dev/null || true
