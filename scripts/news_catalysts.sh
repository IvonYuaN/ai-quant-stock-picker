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
TASK_TIMEOUT_SECONDS="${AQSP_NEWS_TASK_TIMEOUT_SECONDS:-45}"
OUTPUT="${AQSP_NEWS_OUTPUT:-reports/news_catalysts.md}"
ENABLE_LLM_REVIEW="${AQSP_NEWS_ENABLE_LLM_REVIEW:-false}"

LLM_ARGS=()
if [[ "${ENABLE_LLM_REVIEW,,}" =~ ^(1|true|yes|on)$ ]]; then
    LLM_ARGS=(--enable-llm-review)
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
    --notify \
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
        echo "> 🟡 本次消息源超过 ${TASK_TIMEOUT_SECONDS}s 未完成，已自动降级。"
        echo
        echo "## 🧨 高影响事件"
        echo
        echo "- 暂无可靠消息面结论；继续以主链量价、风控约束和人工复核为准。"
        echo
        echo "## ✅ 怎么用"
        echo
        echo "1. 不因本次消息源超时改变系统评分或候选排序。"
        echo "2. 盘前可重新运行一次消息雷达，优先看公告和多源交叉。"
    } > "$OUTPUT"
    log "消息面雷达超时降级: ${OUTPUT}"
elif [ "$NEWS_EXIT" -ne 0 ]; then
    log "[ERROR] 消息面雷达失败: exit=${NEWS_EXIT}"
    exit "$NEWS_EXIT"
fi
log "消息面雷达完成: ${OUTPUT}"

find "$LOG_DIR" -name "news-*.log" -mtime +30 -delete 2>/dev/null || true
