#!/usr/bin/env bash
# 盘中轻量刷新：
# 1. 仅生成盘中候选，不污染正式收盘 ledger
# 2. 刷新当前 Dashboard 展示
# 3. 默认只在交易时段内运行

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
VENV_DIR="${AQSP_INTRADAY_VENV_DIR:-${PROJECT_ROOT}/.venv}"
PYTHON_BIN="${VENV_DIR}/bin/python3"
INITIAL_PROJECT_ROOT="$PROJECT_ROOT"
INITIAL_VENV_DIR="$VENV_DIR"
LOG_DIR="${PROJECT_ROOT}/logs/intraday"
RESULT_LOG="${LOG_DIR}/intraday-$(date +%Y-%m-%d).log"
LOCK_DIR="${PROJECT_ROOT}/.locks/intraday-refresh.lock"
LOCK_INFO_FILE="${LOCK_DIR}/meta.env"
LOCK_STALE_MINUTES="${AQSP_INTRADAY_LOCK_STALE_MINUTES:-30}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RESULT_LOG"
}

is_truthy() {
    local value
    value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$value" =~ ^(1|true|yes|on)$ ]]
}

resolve_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$PROJECT_ROOT" "$1" ;;
    esac
}

lock_age_minutes() {
    local path="$1"
    local now_epoch mtime
    now_epoch="$(date +%s)"
    mtime="$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path")"
    echo $(( (now_epoch - mtime) / 60 ))
}

load_lock_info() {
    if [ -f "$LOCK_INFO_FILE" ]; then
        # shellcheck disable=SC1090
        . "$LOCK_INFO_FILE"
    fi
}

lock_is_stale() {
    if [ ! -d "$LOCK_DIR" ]; then
        return 1
    fi
    local age_minutes pid=""
    age_minutes="$(lock_age_minutes "$LOCK_DIR")"
    load_lock_info
    pid="${LOCK_PID:-}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 1
    fi
    [ "$age_minutes" -ge "$LOCK_STALE_MINUTES" ]
}

mkdir -p "$LOG_DIR" "${PROJECT_ROOT}/.locks"

if [ -d "$LOCK_DIR" ] && lock_is_stale; then
    stale_age="$(lock_age_minutes "$LOCK_DIR")"
    load_lock_info
    log "检测到陈旧盘中刷新锁，自动回收 pid=${LOCK_PID:-unknown} age=${stale_age}min"
    rm -rf -- "$LOCK_DIR"
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "已有盘中刷新任务在运行，跳过"
    exit 0
fi
{
    printf 'LOCK_PID=%q\n' "$$"
    printf 'LOCK_STARTED_AT=%q\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >"$LOCK_INFO_FILE"
trap 'rm -f "$LOCK_INFO_FILE"; rmdir "$LOCK_DIR"' EXIT

if [ ! -d "$VENV_DIR" ]; then
    log "[ERROR] 虚拟环境不存在: $VENV_DIR"
    exit 1
fi

if [ ! -f "$PYTHON_BIN" ]; then
    log "[ERROR] Python 可执行文件不存在: $PYTHON_BIN"
    exit 1
fi

PRESET_AQSP_INTRADAY_SOURCE="${AQSP_INTRADAY_SOURCE:-}"
PRESET_AQSP_INTRADAY_MAX_UNIVERSE="${AQSP_INTRADAY_MAX_UNIVERSE:-${AQSP_MAX_UNIVERSE:-}}"
PRESET_AQSP_INTRADAY_ENABLE_DEBATE="${AQSP_INTRADAY_ENABLE_DEBATE:-}"
PRESET_AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER="${AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER:-}"

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
    # Runtime paths are resolved before loading project secrets/config. A .env
    # file must not silently redirect an explicit release to another worktree.
    PROJECT_ROOT="$INITIAL_PROJECT_ROOT"
    VENV_DIR="$INITIAL_VENV_DIR"
    PYTHON_BIN="${VENV_DIR}/bin/python3"
    log "已加载 .env 配置"
fi

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
export TZ="${TZ:-Asia/Shanghai}"
export AQSP_RUN_TASK_ID="${AQSP_RUN_TASK_ID:-intraday}"
# 盘中入口不接受外部任务 ID，避免残留 daily/live_short 环境改变运行分支。
export AQSP_RUN_TASK_ID="intraday"
export AQSP_NOTIFY="false"
export AQSP_GATE_NOTIFY="false"
# live_short 不能在消息源失败时复用上一交易日的催化缓存。
export AQSP_CATALYST_REPORT_ALLOW_STALE_CACHE="false"
# 实时跨市场网络采集在候选发布后的 sidecar 执行，主选股链禁止等待网络。
export AQSP_MARKET_CONTEXT_LIVE_SOURCE="false"
if [ -n "$PRESET_AQSP_INTRADAY_ENABLE_DEBATE" ]; then
    export AQSP_INTRADAY_ENABLE_DEBATE="$PRESET_AQSP_INTRADAY_ENABLE_DEBATE"
fi
if [ -n "$PRESET_AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER" ]; then
    export AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER="$PRESET_AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER"
fi
# 候选主链不等待 Agent；规则型讨论由候选落盘后的独立 backfill 补齐。
# 盘中主链默认关闭 debate，避免 .env/全局目标开关把讨论延迟带回实时快照。
# Agent 回填在独立子进程中显式开启 advisory 层，外部 LLM 仍默认关闭。
export AQSP_ENABLE_DEBATE="${AQSP_INTRADAY_ENABLE_DEBATE:-false}"
# 组合保护默认开启；仅显式设置盘中本地开发开关才能关闭。
export AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER="${AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER:-false}"
export AQSP_DISABLE_CIRCUIT_BREAKER="${AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER}"
# 盘中默认使用规则 Agent，避免外部 LLM 延迟阻塞实时快照；需要时显式开启。
export AQSP_INTRADAY_DEBATE_ENABLE_LLM="${AQSP_INTRADAY_DEBATE_ENABLE_LLM:-false}"
export AQSP_DEBATE_ENABLE_LLM="${AQSP_INTRADAY_DEBATE_ENABLE_LLM}"
export AQSP_INTRADAY_FAST_SYMBOL_CACHE="${AQSP_INTRADAY_FAST_SYMBOL_CACHE:-${AQSP_RUNTIME_SYMBOL_CACHE:-data/walkforward_production_symbols.json}}"
export AQSP_INTRADAY_FAST_SYMBOL_CSVS="${AQSP_INTRADAY_FAST_SYMBOL_CSVS:-reports/intraday_latest.csv,reports/latest.csv}"
export AQSP_INTRADAY_FAST_FILL_CACHE="${AQSP_INTRADAY_FAST_FILL_CACHE:-true}"

DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
    log "周末(周${DOW})，跳过盘中刷新"
    exit 0
fi

if ! "${PYTHON_BIN}" - <<'AQSP_CALENDAR_PY'
from aqsp.core.time import is_trading_day, today_shanghai
raise SystemExit(0 if is_trading_day(today_shanghai()) else 1)
AQSP_CALENDAR_PY
then
    log "今日非交易日，跳过盘中刷新"
    exit 0
fi

REQUIRE_MARKET_HOURS="${AQSP_INTRADAY_REQUIRE_MARKET_HOURS:-true}"
NOW_HM=$((10#$(date +%H%M)))
if is_truthy "$REQUIRE_MARKET_HOURS"; then
    if ! { [ "$NOW_HM" -ge 935 ] && [ "$NOW_HM" -le 1130 ]; } && \
       ! { [ "$NOW_HM" -ge 1305 ] && [ "$NOW_HM" -le 1457 ]; }; then
        log "当前不在盘中刷新时段，跳过"
        exit 0
    fi
fi

INTRADAY_SOURCE="${AQSP_INTRADAY_SOURCE:-online_first}"
if [ -n "$PRESET_AQSP_INTRADAY_SOURCE" ]; then
    INTRADAY_SOURCE="$PRESET_AQSP_INTRADAY_SOURCE"
fi
if [ "$INTRADAY_SOURCE" = "eastmoney" ] && ! is_truthy "${AQSP_INTRADAY_ALLOW_SINGLE_SOURCE:-false}"; then
    log "盘中 eastmoney 单源分时不稳定，自动切换为 online_first；如需强制单源，设置 AQSP_INTRADAY_ALLOW_SINGLE_SOURCE=true"
    INTRADAY_SOURCE="online_first"
fi
case "$INTRADAY_SOURCE" in
    auto|local_first|online_first|multi|eastmoney|sina|tencent|mootdx)
        ;;
    *)
        log "[ERROR] 盘中源 ${INTRADAY_SOURCE} 不属于 live_short 实时源；拒绝运行，历史源不得进入盘中主链"
        exit 1
        ;;
esac
INTRADAY_MODE="${AQSP_INTRADAY_MODE:-open}"
INTRADAY_LIMIT="${AQSP_INTRADAY_LIMIT:-${AQSP_LIMIT:-10}}"
DEFAULT_INTRADAY_BENCHMARK_SYMBOL="000300"
INTRADAY_BENCHMARK_SYMBOL="${AQSP_INTRADAY_BENCHMARK_SYMBOL:-${AQSP_BENCHMARK_SYMBOL:-$DEFAULT_INTRADAY_BENCHMARK_SYMBOL}}"
if [[ -z "$INTRADAY_BENCHMARK_SYMBOL" || "$INTRADAY_BENCHMARK_SYMBOL" =~ [[:space:]] || ! "$INTRADAY_BENCHMARK_SYMBOL" =~ ^[A-Za-z0-9_.-]{1,20}$ ]]; then
    log "[ERROR] 盘中市场基准配置无效: ${INTRADAY_BENCHMARK_SYMBOL:-<empty>}；请设置 AQSP_INTRADAY_BENCHMARK_SYMBOL"
    exit 1
fi
# 盘中任务按 10 分钟周期运行，默认 7 分钟收尾，给下一轮和清理留余量。
INTRADAY_RUN_TIMEOUT_SECONDS="${AQSP_INTRADAY_RUN_TIMEOUT_SECONDS:-420}"
if ! [[ "$INTRADAY_RUN_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$INTRADAY_RUN_TIMEOUT_SECONDS" -le 0 ]; then
    log "盘中运行超时配置无效(${INTRADAY_RUN_TIMEOUT_SECONDS})，使用 420 秒"
    INTRADAY_RUN_TIMEOUT_SECONDS="420"
fi
# 盘中优先保证一轮在 10 分钟调度周期内完成；扩大扫描请显式配置。
INTRADAY_FAST_MAX_UNIVERSE="${AQSP_INTRADAY_FAST_MAX_UNIVERSE:-40}"
INTRADAY_MAX_UNIVERSE="${AQSP_INTRADAY_MAX_UNIVERSE:-${INTRADAY_FAST_MAX_UNIVERSE}}"
if [ -n "$PRESET_AQSP_INTRADAY_MAX_UNIVERSE" ]; then
    INTRADAY_MAX_UNIVERSE="$PRESET_AQSP_INTRADAY_MAX_UNIVERSE"
fi
if ! [[ "$INTRADAY_MAX_UNIVERSE" =~ ^[0-9]+$ ]] || [ "$INTRADAY_MAX_UNIVERSE" -le 0 ]; then
    log "盘中最大扫描范围无效(${INTRADAY_MAX_UNIVERSE})，使用盘中时效默认 40"
    INTRADAY_MAX_UNIVERSE="40"
elif [ "$INTRADAY_MAX_UNIVERSE" -gt 120 ] && ! is_truthy "${AQSP_INTRADAY_ALLOW_HEAVY_UNIVERSE:-false}"; then
    log "盘中最大扫描范围 ${INTRADAY_MAX_UNIVERSE} 过大，收紧为 120；如需盲扫研究，设置 AQSP_INTRADAY_ALLOW_HEAVY_UNIVERSE=true"
    INTRADAY_MAX_UNIVERSE="120"
fi
INTRADAY_MIN_AVG_AMOUNT="${AQSP_INTRADAY_MIN_AVG_AMOUNT:-${AQSP_MIN_AVG_AMOUNT:-50000000}}"
INTRADAY_MAX_DATA_LAG_DAYS="${AQSP_INTRADAY_MAX_DATA_LAG_DAYS:-1}"
INTRADAY_ALLOW_NOTIFY="${AQSP_INTRADAY_ALLOW_NOTIFY:-false}"
INTRADAY_NOTIFY="${AQSP_INTRADAY_NOTIFY:-false}"

INTRADAY_LEDGER="$(resolve_path "${AQSP_INTRADAY_LEDGER:-data/intraday_predictions.jsonl}")"
INTRADAY_REPORT="$(resolve_path "${AQSP_INTRADAY_REPORT:-reports/intraday_latest.md}")"
INTRADAY_OUTPUT_CSV="$(resolve_path "${AQSP_INTRADAY_OUTPUT_CSV:-reports/intraday_latest.csv}")"
INTRADAY_STATUS="$(resolve_path "${AQSP_INTRADAY_STATUS:-data/intraday_refresh_status.json}")"
INTRADAY_DASHBOARD_HTML="$(resolve_path "${AQSP_INTRADAY_DASHBOARD_HTML:-dist/dashboard/index.html}")"
INTRADAY_DASHBOARD_DB="$(resolve_path "${AQSP_INTRADAY_DASHBOARD_DB:-dist/dashboard/aqsp.db}")"
HOME_SNAPSHOT_PATH="$(resolve_path "${AQSP_HOME_SNAPSHOT_PATH:-data/runtime/home_dashboard_snapshot.json}")"
HOME_SNAPSHOT_INDEX_PATH="$(resolve_path "${AQSP_HOME_SNAPSHOT_INDEX_PATH:-data/runtime/home_dashboard_snapshot_index.json}")"
PAPER_LEDGER="$(resolve_path "${AQSP_PAPER_LEDGER:-data/paper_trades.jsonl}")"
INTRADAY_NEWS_SCRIPT="${AQSP_INTRADAY_NEWS_SCRIPT:-${PROJECT_ROOT}/scripts/news_catalysts.sh}"
INTRADAY_NEWS_OUTPUT="$(resolve_path "${AQSP_INTRADAY_NEWS_OUTPUT:-${AQSP_NEWS_OUTPUT:-reports/news_catalysts.md}}")"
INTRADAY_NEWS_JSON_OUTPUT="$(resolve_path "${AQSP_INTRADAY_NEWS_JSON_OUTPUT:-${AQSP_NEWS_JSON_OUTPUT:-data/runtime/news_catalysts_latest.json}}")"
INTRADAY_NEWS_TASK_TIMEOUT_SECONDS="${AQSP_INTRADAY_NEWS_TASK_TIMEOUT_SECONDS:-20}"
INTRADAY_NEWS_SOURCE_TIMEOUT_SECONDS="${AQSP_INTRADAY_NEWS_SOURCE_TIMEOUT_SECONDS:-2}"
INTRADAY_NEWS_MAX_EVENTS="${AQSP_INTRADAY_NEWS_MAX_EVENTS:-3}"
INTRADAY_NEWS_MAX_SYMBOLS="${AQSP_INTRADAY_NEWS_MAX_SYMBOLS:-3}"
INTRADAY_NEWS_MAX_NEWS_AGE_DAYS="${AQSP_INTRADAY_NEWS_MAX_NEWS_AGE_DAYS:-0}"
if ! [[ "$INTRADAY_NEWS_MAX_SYMBOLS" =~ ^[0-9]+$ ]] || [ "$INTRADAY_NEWS_MAX_SYMBOLS" -le 0 ]; then
    INTRADAY_NEWS_MAX_SYMBOLS="3"
fi
REALTIME_CROSS_MARKET_SCRIPT="${AQSP_REALTIME_CROSS_MARKET_SCRIPT:-${PROJECT_ROOT}/scripts/collect_realtime_cross_market.py}"
REALTIME_CROSS_MARKET_PATH="$(resolve_path "${AQSP_REALTIME_CROSS_MARKET_PATH:-data/runtime/realtime_cross_market_context.json}")"
REALTIME_CROSS_MARKET_TIMEOUT_SECONDS="${AQSP_REALTIME_CROSS_MARKET_TIMEOUT_SECONDS:-8}"
REALTIME_CROSS_MARKET_SOURCE_TIMEOUT_SECONDS="${AQSP_REALTIME_CROSS_MARKET_SOURCE_TIMEOUT_SECONDS:-0.75}"
DEBATE_RESULTS="$(resolve_path "${AQSP_DEBATE_RESULTS:-data/debate_results.jsonl}")"
DEBATE_BACKFILL_LOG="${LOG_DIR}/debate-backfill-$(date +%Y-%m-%d).log"
DEBATE_BACKFILL_LOCK="${PROJECT_ROOT}/.locks/intraday-debate-backfill.lock"
DEBATE_BACKFILL_LOCK_INFO="${DEBATE_BACKFILL_LOCK}/meta.env"
DEBATE_BACKFILL_STALE_MINUTES="${AQSP_INTRADAY_DEBATE_BACKFILL_STALE_MINUTES:-30}"
DEBATE_BACKFILL_TIMEOUT_SECONDS="${AQSP_INTRADAY_DEBATE_BACKFILL_TIMEOUT_SECONDS:-120}"
if ! [[ "$DEBATE_BACKFILL_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$DEBATE_BACKFILL_TIMEOUT_SECONDS" -le 0 ]; then
    DEBATE_BACKFILL_TIMEOUT_SECONDS="120"
fi
QUALITY_GATE_TIMEOUT_SECONDS="${AQSP_INTRADAY_QUALITY_GATE_TIMEOUT_SECONDS:-30}"
if ! [[ "$QUALITY_GATE_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$QUALITY_GATE_TIMEOUT_SECONDS" -le 0 ]; then
    QUALITY_GATE_TIMEOUT_SECONDS="30"
fi

mkdir -p \
    "$(dirname "$INTRADAY_LEDGER")" \
    "$(dirname "$INTRADAY_REPORT")" \
    "$(dirname "$INTRADAY_OUTPUT_CSV")" \
    "$(dirname "$INTRADAY_STATUS")" \
    "$(dirname "$INTRADAY_DASHBOARD_HTML")" \
    "$(dirname "$INTRADAY_DASHBOARD_DB")" \
    "$(dirname "$INTRADAY_NEWS_OUTPUT")" \
    "$(dirname "$INTRADAY_NEWS_JSON_OUTPUT")" \
    "$(dirname "$REALTIME_CROSS_MARKET_PATH")" \
    "$(dirname "$DEBATE_RESULTS")"

TMP_ROOT="${PROJECT_ROOT}/.tmp"
mkdir -p "$TMP_ROOT"
TMP_DIR="$(mktemp -d "${TMP_ROOT}/intraday-refresh.XXXXXX")"
TMP_INTRADAY_LEDGER="${TMP_DIR}/intraday_predictions.jsonl"
TMP_INTRADAY_REPORT="${TMP_DIR}/intraday_latest.md"
TMP_INTRADAY_OUTPUT_CSV="${TMP_DIR}/intraday_latest.csv"
QUALITY_GATE_SUMMARY="${TMP_DIR}/quality_gate.json"

replace_intraday_artifact() {
    local src="$1"
    local dest="$2"
    local label="$3"
    if [ -f "$src" ]; then
        mv -f "$src" "$dest"
        log "已更新${label}: ${dest}"
    else
        log "未生成${label}，保留上一版: ${dest}"
    fi
}

debate_backfill_lock_age_minutes() {
    local path="$1"
    local now_epoch mtime
    now_epoch="$(date +%s)"
    mtime="$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path")"
    echo $(( (now_epoch - mtime) / 60 ))
}

debate_backfill_lock_is_stale() {
    if [ ! -d "$DEBATE_BACKFILL_LOCK" ]; then
        return 1
    fi
    local age_minutes pid=""
    age_minutes="$(debate_backfill_lock_age_minutes "$DEBATE_BACKFILL_LOCK")"
    if [ -f "$DEBATE_BACKFILL_LOCK_INFO" ]; then
        # shellcheck disable=SC1090
        . "$DEBATE_BACKFILL_LOCK_INFO"
    fi
    pid="${DEBATE_LOCK_PID:-}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 1
    fi
    [ "$age_minutes" -ge "$DEBATE_BACKFILL_STALE_MINUTES" ]
}

acquire_debate_backfill_lock() {
    if [ -d "$DEBATE_BACKFILL_LOCK" ] && debate_backfill_lock_is_stale; then
        log "检测到陈旧 Agent 回填锁，自动回收"
        rm -rf -- "$DEBATE_BACKFILL_LOCK"
    fi
    if ! mkdir "$DEBATE_BACKFILL_LOCK" 2>/dev/null; then
        return 1
    fi
    {
        printf 'DEBATE_LOCK_PID=%q\n' "$$"
        printf 'DEBATE_LOCK_STARTED_AT=%q\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    } >"$DEBATE_BACKFILL_LOCK_INFO"
    return 0
}

release_debate_backfill_lock() {
    rm -f "$DEBATE_BACKFILL_LOCK_INFO"
    rmdir "$DEBATE_BACKFILL_LOCK" 2>/dev/null || true
}

refresh_debate_backfill_lock_owner() {
    {
        printf 'DEBATE_LOCK_PID=%q\n' "${BASHPID:-$$}"
        printf 'DEBATE_LOCK_STARTED_AT=%q\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    } >"$DEBATE_BACKFILL_LOCK_INFO"
}

launch_intraday_debate_backfill() {
    if ! is_truthy "${AQSP_INTRADAY_DEBATE_BACKFILL:-true}"; then
        log "盘中 Agent 讨论回填已关闭"
        return 0
    fi
    if [ ! -f "$INTRADAY_OUTPUT_CSV" ]; then
        log "盘中 CSV 不存在，跳过 Agent 讨论回填"
        return 0
    fi
    if ! acquire_debate_backfill_lock; then
        log "已有盘中 Agent 讨论回填在运行，跳过"
        return 0
    fi
    local max_candidates
    max_candidates="${AQSP_INTRADAY_DEBATE_BACKFILL_MAX_CANDIDATES:-5}"
    local -a force_arg=()
    if is_truthy "${AQSP_INTRADAY_DEBATE_BACKFILL_FORCE:-true}"; then
        force_arg=(--force)
    fi
    local -a run_backfill
    run_backfill=(
        timeout --foreground --signal=TERM --kill-after=10s
        "${DEBATE_BACKFILL_TIMEOUT_SECONDS}s"
        env \
        "AQSP_ENABLE_DEBATE=true" \
        "AQSP_DEBATE_ENABLE_LLM=${AQSP_INTRADAY_DEBATE_ENABLE_LLM}"
        "$PYTHON_BIN" "${PROJECT_ROOT}/scripts/backfill_intraday_debate.py"
        --input-csv "$INTRADAY_OUTPUT_CSV"
        --output "$DEBATE_RESULTS"
        --task-id "${AQSP_RUN_TASK_ID}"
        --max-candidates "$max_candidates"
        "${force_arg[@]}"
    )
    if is_truthy "${AQSP_INTRADAY_DEBATE_BACKFILL_BACKGROUND:-true}"; then
        (
            refresh_debate_backfill_lock_owner
            trap release_debate_backfill_lock EXIT
            cd "$PROJECT_ROOT"
            if "${run_backfill[@]}" >>"$DEBATE_BACKFILL_LOG" 2>&1; then
                if refresh_home_dashboard_snapshot; then
                    log "Agent 讨论回填完成，首页快照已刷新"
                else
                    log "[WARN] Agent 讨论回填完成，但首页快照刷新失败"
                fi
            else
                backfill_exit_code=$?
                log "[WARN] Agent 讨论回填失败，保留无讨论快照，退出码: ${backfill_exit_code}"
            fi
        ) &
        log "已后台启动盘中 Agent 讨论回填: ${DEBATE_BACKFILL_LOG}"
    else
        (
            refresh_debate_backfill_lock_owner
            trap release_debate_backfill_lock EXIT
            cd "$PROJECT_ROOT"
            if "${run_backfill[@]}" >>"$DEBATE_BACKFILL_LOG" 2>&1; then
                if refresh_home_dashboard_snapshot; then
                    log "Agent 讨论回填完成，首页快照已刷新"
                else
                    log "[WARN] Agent 讨论回填完成，但首页快照刷新失败"
                fi
            else
                backfill_exit_code=$?
                log "[WARN] Agent 讨论回填失败，保留无讨论快照，退出码: ${backfill_exit_code}"
            fi
        )
        log "盘中 Agent 讨论回填完成: ${DEBATE_BACKFILL_LOG}"
    fi
}

refresh_home_dashboard_snapshot() {
    if ! is_truthy "${AQSP_HOME_SNAPSHOT_ENABLED:-true}"; then
        log "首页快照刷新已关闭"
        return 0
    fi
    if "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/write_home_snapshot.py" \
        --task-id "${AQSP_RUN_TASK_ID}" \
        --output "${HOME_SNAPSHOT_PATH}" \
        --index-output "${HOME_SNAPSHOT_INDEX_PATH}" >>"${RESULT_LOG}" 2>&1; then
        log "首页快照已刷新"
        return 0
    else
        log "[WARN] 首页快照刷新失败，保留上一版快照"
        return 1
    fi
}

collect_intraday_news_symbols() {
    if [ -n "${AQSP_INTRADAY_NEWS_SYMBOLS:-}" ]; then
        printf '%s\n' "$AQSP_INTRADAY_NEWS_SYMBOLS"
        return 0
    fi
    if [ ! -f "$INTRADAY_OUTPUT_CSV" ]; then
        return 0
    fi
    awk -F',' -v limit="$INTRADAY_NEWS_MAX_SYMBOLS" '
        NR == 1 {
            for (i = 1; i <= NF; i++) {
                if ($i == "symbol") symbol_column = i
            }
            next
        }
        symbol_column && $symbol_column != "" && $symbol_column != "__RUN__" {
            gsub(/^"|"$/, "", $symbol_column)
            if (count < limit && seen[$symbol_column]++ == 0) {
                if (count++ > 0) printf ","
                printf "%s", $symbol_column
            }
        }
        END { if (count > 0) printf "\n" }
    ' "$INTRADAY_OUTPUT_CSV"
}

refresh_intraday_news_catalysts() {
    NEWS_CATALYST_STATUS="skipped"
    NEWS_CATALYST_EXIT_CODE="0"
    NEWS_CATALYST_WARNING=""

    if ! is_truthy "${AQSP_INTRADAY_NEWS_REFRESH:-true}"; then
        log "盘中消息面刷新已关闭"
        return 0
    fi
    if [ ! -f "$INTRADAY_NEWS_SCRIPT" ]; then
        NEWS_CATALYST_WARNING="news_catalysts.sh 不存在"
        log "[WARN] ${NEWS_CATALYST_WARNING}，跳过本轮消息面刷新"
        return 0
    fi

    local news_symbols
    news_symbols="$(collect_intraday_news_symbols)"
    log "候选已落盘，开始刷新当前日消息面 JSON/Markdown（最多 ${INTRADAY_NEWS_MAX_EVENTS} 条）"
    set +e
    env \
        AQSP_PROJECT_ROOT="$PROJECT_ROOT" \
        AQSP_RUN_TASK_ID="$AQSP_RUN_TASK_ID" \
        AQSP_NEWS_SYMBOLS="$news_symbols" \
        AQSP_NEWS_OUTPUT="$INTRADAY_NEWS_OUTPUT" \
        AQSP_NEWS_JSON_OUTPUT="$INTRADAY_NEWS_JSON_OUTPUT" \
        AQSP_NEWS_TASK_TIMEOUT_SECONDS="$INTRADAY_NEWS_TASK_TIMEOUT_SECONDS" \
        AQSP_NEWS_SOURCE_TIMEOUT_SECONDS="$INTRADAY_NEWS_SOURCE_TIMEOUT_SECONDS" \
        AQSP_NEWS_MAX_EVENTS="$INTRADAY_NEWS_MAX_EVENTS" \
        AQSP_NEWS_MAX_NEWS_AGE_DAYS="$INTRADAY_NEWS_MAX_NEWS_AGE_DAYS" \
        AQSP_NEWS_ENABLE_LLM_REVIEW="false" \
        AQSP_NEWS_LLM_TIMEOUT_SECONDS="1" \
        AQSP_NEWS_MAX_LLM_REVIEW_EVENTS="0" \
        AQSP_NEWS_NOTIFY="false" \
        AQSP_ALLOW_NON_TRADING_NEWS_NOTIFY="false" \
        AQSP_NOTIFY="false" \
        AQSP_GATE_NOTIFY="false" \
        timeout --signal=TERM --kill-after=3s \
        "${INTRADAY_NEWS_TASK_TIMEOUT_SECONDS}s" \
        bash "$INTRADAY_NEWS_SCRIPT" 2>&1 | tee -a "$RESULT_LOG"
    local -a news_pipe_status=("${PIPESTATUS[@]}")
    local news_exit_code="${news_pipe_status[0]:-1}"
    local news_tee_exit_code="${news_pipe_status[1]:-1}"
    set -e

    NEWS_CATALYST_EXIT_CODE="$news_exit_code"
    if [ "$news_tee_exit_code" -ne 0 ]; then
        log "[WARN] 盘中消息面结果日志管道退出码: ${news_tee_exit_code}"
    fi
    if [ "$news_exit_code" -eq 0 ]; then
        NEWS_CATALYST_STATUS="refreshed"
        log "盘中消息面刷新完成: ${INTRADAY_NEWS_JSON_OUTPUT} / ${INTRADAY_NEWS_OUTPUT}"
    else
        NEWS_CATALYST_STATUS="warning"
        NEWS_CATALYST_WARNING="消息面刷新失败，保留候选和首页快照"
        log "[WARN] ${NEWS_CATALYST_WARNING}，退出码: ${news_exit_code}"
    fi
    return 0
}

refresh_realtime_cross_market_context() {
    if ! is_truthy "${AQSP_INTRADAY_MARKET_CONTEXT_REFRESH:-true}"; then
        log "盘中跨市场 sidecar 已关闭"
        return 0
    fi
    if [ ! -f "$REALTIME_CROSS_MARKET_SCRIPT" ]; then
        log "[WARN] 实时跨市场 sidecar 不存在，保留候选"
        return 0
    fi
    set +e
    timeout --signal=TERM --kill-after=2s \
        "${REALTIME_CROSS_MARKET_TIMEOUT_SECONDS}s" \
        "$PYTHON_BIN" "$REALTIME_CROSS_MARKET_SCRIPT" \
        --output "$REALTIME_CROSS_MARKET_PATH" \
        --timeout-seconds "$REALTIME_CROSS_MARKET_SOURCE_TIMEOUT_SECONDS" \
        >>"$RESULT_LOG" 2>&1
    local exit_code=$?
    set -e
    if [ "$exit_code" -ne 0 ]; then
        log "[WARN] 实时跨市场 sidecar 失败(${exit_code})，候选保持可用"
        return 0
    fi
    log "实时跨市场 sidecar 已刷新"
}

apply_intraday_quality_gate() {
    local csv_path="$1"
    local ledger_path="$2"
    local report_path="$3"
    local summary_path="$4"
    INTRADAY_QUALITY_CSV="$csv_path" \
    INTRADAY_QUALITY_LEDGER="$ledger_path" \
    INTRADAY_QUALITY_REPORT="$report_path" \
    INTRADAY_QUALITY_SUMMARY="$summary_path" \
    INTRADAY_QUALITY_BENCHMARK="$INTRADAY_BENCHMARK_SYMBOL" \
    INTRADAY_QUALITY_MAX_LAG="$INTRADAY_MAX_DATA_LAG_DAYS" \
    timeout --foreground --signal=TERM --kill-after=5s \
        "${QUALITY_GATE_TIMEOUT_SECONDS}s" \
        "$PYTHON_BIN" - <<'AQSP_INTRADAY_QUALITY_GATE_PY'
# AQSP_INTRADAY_QUALITY_GATE
import csv
import json
import os
import re
from pathlib import Path


def clean(value: object) -> str:
    return str(value or "").strip()


csv_path = Path(os.environ["INTRADAY_QUALITY_CSV"])
ledger_path = Path(os.environ["INTRADAY_QUALITY_LEDGER"])
report_path = Path(os.environ["INTRADAY_QUALITY_REPORT"])
summary_path = Path(os.environ["INTRADAY_QUALITY_SUMMARY"])
benchmark = os.environ["INTRADAY_QUALITY_BENCHMARK"]

if not csv_path.is_file():
    raise SystemExit(f"盘中质量门输入 CSV 不存在: {csv_path}")

with csv_path.open("r", encoding="utf-8", newline="") as handle:
    reader = csv.DictReader(handle)
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)

run_rows = [row for row in rows if clean(row.get("symbol")) == "__RUN__"]
run_row = run_rows[0] if run_rows else {}
provenance = {
    "requested_source": clean(run_row.get("run_requested_source")),
    "actual_source": clean(run_row.get("run_actual_source")),
    "workload": clean(run_row.get("run_workload")),
    "latest_trade_date": clean(run_row.get("run_data_latest_trade_date")),
    "freshness_tier": clean(run_row.get("run_source_freshness_tier")).lower(),
    "coverage_tier": clean(run_row.get("run_source_coverage_tier")),
    "local_status": clean(run_row.get("run_source_local_status")),
}
provenance_status = (
    "verified"
    if all(
        provenance[key]
        for key in (
            "requested_source",
            "actual_source",
            "workload",
            "latest_trade_date",
            "freshness_tier",
            "coverage_tier",
            "local_status",
        )
    )
    and provenance["workload"] == "live_short"
    else "unknown"
)
try:
    lag_days = int(float(clean(run_row.get("run_data_lag_days")) or "0"))
except ValueError:
    lag_days = -1
source_tier = clean(run_row.get("run_source_freshness_tier")).lower()
fresh_tiers = {"realtime", "terminal_realtime"}
watch_tiers = {"delayed_realtime"}
if lag_days < 0 or not source_tier or provenance_status != "verified":
    freshness_status = "unknown"
elif lag_days > 0 or source_tier not in fresh_tiers | watch_tiers:
    freshness_status = "stale"
elif source_tier in watch_tiers:
    freshness_status = "watch"
else:
    freshness_status = "fresh"

quality_watch_tokens = ("watch", "warning", "警告", "质量")
stale_tokens = ("stale", "过期", "freshness", "新鲜度", "延迟")
not_executable_tokens = (
    "not_executable",
    "不可成交",
    "停牌",
    "涨停",
    "跌停",
)


def quality_action(row: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    values = " ".join(
        clean(row.get(key)).lower()
        for key in (
            "status",
            "candidate_status",
            "candidate_blocker",
            "not_executable_reason",
            "data_quality_status",
            "data_quality_alerts",
        )
    )
    reasons: list[str] = []
    if any(token in values for token in not_executable_tokens):
        reasons.append("not_executable")
    if freshness_status == "unknown":
        reasons.append("freshness_unknown")
    elif freshness_status == "stale" or any(token in values for token in stale_tokens):
        reasons.append("stale_freshness")
    elif freshness_status == "watch":
        reasons.append("freshness_watch")
    if clean(row.get("data_quality_status")).lower() in {"critical", "stale"}:
        reasons.append("data_quality_critical")
    if clean(row.get("data_quality_status")).lower() == "watch" or any(
        token in values for token in quality_watch_tokens
    ):
        reasons.append("quality_watch")
    reasons = list(dict.fromkeys(reasons))
    if any(
        reason in reasons
        for reason in (
            "not_executable",
            "freshness_unknown",
            "stale_freshness",
            "data_quality_critical",
        )
    ):
        return "blocked", tuple(reasons)
    if reasons:
        return "observe", tuple(reasons)
    return "clean", ()


candidate_rows: list[tuple[dict[str, str], str, tuple[str, ...], int]] = []
watch_count = 0
blocked_count = 0
changed_count = 0
for candidate_index, row in enumerate(
    (row for row in rows if clean(row.get("symbol")) not in {"", "__RUN__"}),
    start=1,
):
    action, reasons = quality_action(row)
    row["quality_gate_action"] = action
    row["quality_gate_reasons"] = ";".join(reasons)
    row["paper_review_eligible"] = "true" if action == "clean" else "false"
    if action != "clean":
        old_priority = clean(row.get("candidate_review_priority"))
        row["candidate_review_priority"] = "low"
        row["candidate_next_step"] = "数据质量恢复新鲜且可成交后，再评估纸面复核"
        row["candidate_review_window"] = "质量门恢复后"
        row["portfolio_action"] = "downgrade"
        if action == "blocked":
            row["candidate_status"] = "质量阻塞"
            row["candidate_blocker"] = "质量门阻塞: " + "、".join(reasons)
            blocked_count += 1
        else:
            row["candidate_status"] = "质量观察"
            row["candidate_blocker"] = ""
            watch_count += 1
        changed_count += int(old_priority == "high")
    candidate_rows.append((row, action, reasons, candidate_index))

all_candidates_blocked = bool(candidate_rows) and blocked_count == len(candidate_rows)

if run_row:
    run_row["benchmark_symbol"] = benchmark
    run_row["freshness_status"] = freshness_status
    run_row["quality_gate_status"] = (
        "blocked"
        if freshness_status in {"unknown", "stale"} or all_candidates_blocked
        else "degraded"
        if watch_count or freshness_status == "watch"
        else "passed"
    )
    run_row["quality_gate_watch_count"] = str(watch_count)
    run_row["quality_gate_blocked_count"] = str(blocked_count)

all_fields = list(dict.fromkeys(fieldnames + [
    "candidate_status",
    "candidate_blocker",
    "candidate_next_step",
    "candidate_review_window",
    "portfolio_action",
    "quality_gate_action",
    "quality_gate_reasons",
    "paper_review_eligible",
    "benchmark_symbol",
    "freshness_status",
    "quality_gate_status",
    "quality_gate_watch_count",
    "quality_gate_blocked_count",
]))
with csv_path.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=all_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

if ledger_path.is_file():
    ledger_rows: list[dict[str, object]] = []
    with ledger_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                ledger_rows.append(json.loads(line))
            except json.JSONDecodeError:
                ledger_rows.append({"_raw_line": line.rstrip("\n")})
    actions_by_symbol = {
        clean(row.get("symbol")): (action, reasons)
        for row, action, reasons, _ in candidate_rows
    }
    for row in ledger_rows:
        symbol = clean(row.get("symbol"))
        action, reasons = actions_by_symbol.get(symbol, ("clean", ()))
        if action == "clean":
            continue
        row["quality_gate_action"] = action
        row["quality_gate_reasons"] = list(reasons)
        row["paper_review_eligible"] = False
        row["candidate_review_priority"] = "low"
        row["candidate_status"] = "质量阻塞" if action == "blocked" else "质量观察"
        row["candidate_next_step"] = "数据质量恢复新鲜且可成交后，再评估纸面复核"
        row["candidate_review_window"] = "质量门恢复后"
        row["portfolio_action"] = "downgrade"
        if action == "blocked":
            row["candidate_blocker"] = "质量门阻塞: " + "、".join(reasons)
    tmp_ledger = ledger_path.with_suffix(ledger_path.suffix + ".quality.tmp")
    with tmp_ledger.open("w", encoding="utf-8") as handle:
        for row in ledger_rows:
            if "_raw_line" in row:
                handle.write(str(row["_raw_line"]) + "\n")
            else:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_ledger.replace(ledger_path)

if report_path.is_file():
    report = report_path.read_text(encoding="utf-8")
    for _, action, reasons, candidate_index in candidate_rows:
        if action == "clean":
            continue
        section = re.compile(rf"(## {candidate_index}\..*?)(?=\n## \d+\.|\Z)", re.S)
        replacement_note = (
            "- 质量门: "
            + ("阻塞" if action == "blocked" else "观察")
            + "（"
            + "、".join(reasons)
            + "，不进入高优先级纸面复核）"
        )
        match = section.search(report)
        if match:
            body = match.group(1).replace("高优先级", "低优先级")
            body = body.rstrip() + "\n" + replacement_note + "\n"
            report = report[: match.start()] + body + report[match.end() :]
    report += (
        "\n\n## 盘中质量门\n"
        f"- 市场基准: {benchmark}\n"
        f"- freshness: {freshness_status}（数据延迟 {lag_days} 天，来源层 {source_tier or 'unknown'}）\n"
        f"- quality_gate: {'blocked' if freshness_status in {'unknown', 'stale'} or all_candidates_blocked else 'degraded' if watch_count or blocked_count or freshness_status == 'watch' else 'passed'}\n"
        f"- 观察降级: {watch_count}；阻塞: {blocked_count}\n"
        "- 质量门只调整观察/纸面复核资格，不改写确定性评分，不触发自动下单。\n"
    )
    report_path.write_text(report, encoding="utf-8")

summary = {
    "availability_status": "available"
    if run_row and "symbol" in fieldnames and provenance_status == "verified"
    else "unavailable",
    "status": (
        "blocked"
        if freshness_status in {"unknown", "stale"} or all_candidates_blocked
        else "degraded"
        if watch_count or freshness_status == "watch"
        else "passed"
    ),
    "benchmark_symbol": benchmark,
    "freshness_status": freshness_status,
    "lag_days": lag_days,
    "source_freshness_tier": source_tier,
    "provenance_status": provenance_status,
    "provenance": provenance,
    "checked_count": len(candidate_rows),
    "watch_count": watch_count,
    "blocked_count": blocked_count,
    "downgraded_count": watch_count + blocked_count,
    "high_priority_downgraded_count": changed_count,
    "max_allowed_lag_days": int(os.environ["INTRADAY_QUALITY_MAX_LAG"]),
}
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
if freshness_status in {"unknown", "stale"}:
    raise SystemExit(2)
AQSP_INTRADAY_QUALITY_GATE_PY
}

promote_observation_only_if_safe() {
    # Only promote a complete, fresh snapshot; stale data remains quarantined.
    if [ ! -f "$TMP_INTRADAY_OUTPUT_CSV" ] || \
       [ ! -f "$TMP_INTRADAY_REPORT" ] || \
       [ ! -f "$TMP_INTRADAY_LEDGER" ] || \
       [ ! -f "$QUALITY_GATE_SUMMARY" ]; then
        log "[WARN] 盘中观察晋级缺少关键临时产物，保留上一版盘中产物"
        return 1
    fi
    if ! env \
        "INTRADAY_OBSERVATION_CSV=$TMP_INTRADAY_OUTPUT_CSV" \
        "INTRADAY_OBSERVATION_LEDGER=$TMP_INTRADAY_LEDGER" \
        "INTRADAY_OBSERVATION_REPORT=$TMP_INTRADAY_REPORT" \
        "INTRADAY_OBSERVATION_SUMMARY=$QUALITY_GATE_SUMMARY" \
        "INTRADAY_OBSERVATION_REASON=${OBSERVATION_REASON:-质量门未通过}" \
        "$PYTHON_BIN" - <<'AQSP_INTRADAY_OBSERVATION_PY'
import csv
import json
import os
from pathlib import Path


def clean(value: object) -> str:
    return str(value or "").strip()


csv_path = Path(os.environ["INTRADAY_OBSERVATION_CSV"])
ledger_path = Path(os.environ["INTRADAY_OBSERVATION_LEDGER"])
report_path = Path(os.environ["INTRADAY_OBSERVATION_REPORT"])
summary_path = Path(os.environ["INTRADAY_OBSERVATION_SUMMARY"])
summary = json.loads(summary_path.read_text(encoding="utf-8"))
if summary.get("availability_status") != "available":
    raise SystemExit("盘中产物可用性未通过，拒绝观察晋级")
if summary.get("freshness_status") not in {"fresh", "watch"}:
    raise SystemExit(
        "盘中产物新鲜度未通过观察晋级: "
        + clean(summary.get("freshness_status"))
    )

with csv_path.open("r", encoding="utf-8", newline="") as handle:
    reader = csv.DictReader(handle)
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)
if "symbol" not in fieldnames or not any(
    clean(row.get("symbol")) == "__RUN__" for row in rows
):
    raise SystemExit("盘中 CSV 缺少有效运行标记，拒绝观察晋级")

all_fields = list(
    dict.fromkeys(
        fieldnames
        + [
            "intraday_artifact_mode",
            "observation_only",
            "quality_gate_action",
            "quality_gate_reasons",
            "paper_review_eligible",
            "candidate_status",
            "candidate_blocker",
            "candidate_next_step",
            "candidate_review_window",
            "candidate_review_priority",
            "portfolio_action",
        ]
    )
)
for row in rows:
    symbol = clean(row.get("symbol"))
    row["intraday_artifact_mode"] = "observation_only"
    row["observation_only"] = "true"
    if symbol == "__RUN__":
        row["quality_gate_status"] = "observation_only"
        continue
    action = clean(row.get("quality_gate_action"))
    if action != "blocked":
        action = "observe"
        row["candidate_status"] = "盘中观察"
        row["candidate_blocker"] = "质量门未通过后仅观察，不作推荐"
    else:
        row["candidate_status"] = "质量阻塞"
    reasons = [
        token
        for token in clean(row.get("quality_gate_reasons")).split(";")
        if token
    ]
    if "observation_only" not in reasons:
        reasons.append("observation_only")
    row["quality_gate_action"] = action
    row["quality_gate_reasons"] = ";".join(dict.fromkeys(reasons))
    row["paper_review_eligible"] = "false"
    row["candidate_review_priority"] = "low"
    row["candidate_next_step"] = "质量门恢复后重新评估；当前仅观察，不作推荐"
    row["candidate_review_window"] = "质量门恢复后"
    row["portfolio_action"] = "observation_only"

csv_tmp = csv_path.with_suffix(csv_path.suffix + ".observation.tmp")
with csv_tmp.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=all_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
csv_tmp.replace(csv_path)

ledger_rows: list[dict[str, object]] = []
with ledger_path.open("r", encoding="utf-8") as handle:
    for line in handle:
        if not line.strip():
            continue
        try:
            ledger_rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit("盘中 ledger 含无效 JSON，拒绝观察晋级") from exc
for row in ledger_rows:
    action = clean(row.get("quality_gate_action"))
    if action != "blocked":
        action = "observe"
        row["candidate_status"] = "盘中观察"
        row["candidate_blocker"] = "质量门未通过后仅观察，不作推荐"
    else:
        row["candidate_status"] = "质量阻塞"
    reasons = row.get("quality_gate_reasons", [])
    if isinstance(reasons, str):
        reasons = [token for token in reasons.split(";") if token]
    elif isinstance(reasons, list):
        reasons = [clean(token) for token in reasons if clean(token)]
    else:
        reasons = []
    if "observation_only" not in reasons:
        reasons.append("observation_only")
    row["intraday_artifact_mode"] = "observation_only"
    row["observation_only"] = True
    row["quality_gate_action"] = action
    row["quality_gate_reasons"] = list(dict.fromkeys(reasons))
    row["paper_review_eligible"] = False
    row["candidate_review_priority"] = "low"
    row["candidate_next_step"] = "质量门恢复后重新评估；当前仅观察，不作推荐"
    row["candidate_review_window"] = "质量门恢复后"
    row["portfolio_action"] = "observation_only"
ledger_tmp = ledger_path.with_suffix(ledger_path.suffix + ".observation.tmp")
with ledger_tmp.open("w", encoding="utf-8") as handle:
    for row in ledger_rows:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
ledger_tmp.replace(ledger_path)

banner = (
    "## 盘中产物模式\n"
    "- observation_only: true\n"
    "- 当前仅作为实时盘中观察展示，不是推荐、纸面复核或正式 ledger 输入。\n"
    f"- 原因: {os.environ['INTRADAY_OBSERVATION_REASON']}\n"
    "- 质量门恢复后，必须重新运行质量判断才能恢复候选资格。\n\n"
)
report = report_path.read_text(encoding="utf-8")
if "## 盘中产物模式" not in report:
    report_path.write_text(banner + report, encoding="utf-8")
AQSP_INTRADAY_OBSERVATION_PY
    then
        log "[WARN] 盘中观察标记失败，保留上一版盘中产物"
        return 1
    fi
    replace_intraday_artifact "$TMP_INTRADAY_LEDGER" "$INTRADAY_LEDGER" "盘中 observation-only ledger"
    replace_intraday_artifact "$TMP_INTRADAY_REPORT" "$INTRADAY_REPORT" "盘中 observation-only 报告"
    replace_intraday_artifact "$TMP_INTRADAY_OUTPUT_CSV" "$INTRADAY_OUTPUT_CSV" "盘中 observation-only CSV"
}

promote_provisional_observation_only() {
    # The run may flush the provisional CSV/report before producing a ledger.
    # Keep that fresh evidence visible, but never promote it to recommendation.
    if [ ! -f "$INTRADAY_OUTPUT_CSV" ] || [ ! -f "$INTRADAY_REPORT" ]; then
        return 1
    fi
    if ! "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/mark_intraday_observation.py" \
        --csv "$INTRADAY_OUTPUT_CSV" \
        --report "$INTRADAY_REPORT" \
        --reason "盘中任务超时或后处理未完成，临时 ledger 不完整" \
        --minimum-mtime "$START_TIME" >>"${RESULT_LOG}" 2>&1; then
        return 1
    fi
    OBSERVATION_ONLY="true"
    OBSERVATION_REASON="最新临时 CSV 已晋级 observation-only；盘中 ledger 未完整生成"
    return 0
}

quality_gate_requires_observation() {
    if [ ! -f "$QUALITY_GATE_SUMMARY" ]; then
        return 1
    fi
    INTRADAY_QUALITY_STATUS_PATH="$QUALITY_GATE_SUMMARY" \
    "$PYTHON_BIN" - <<'AQSP_INTRADAY_QUALITY_STATUS_PY'
import json
import os
from pathlib import Path

payload = json.loads(
    Path(os.environ["INTRADAY_QUALITY_STATUS_PATH"]).read_text(encoding="utf-8")
)
raise SystemExit(0 if payload.get("status") == "blocked" else 1)
AQSP_INTRADAY_QUALITY_STATUS_PY
}

write_intraday_status() {
    local status="$1"
    local reason="$2"
    local exit_code="$3"
    INTRADAY_STATUS_PATH="$INTRADAY_STATUS" \
    INTRADAY_STATUS_VALUE="$status" \
    INTRADAY_STATUS_REASON="$reason" \
    INTRADAY_STATUS_EXIT_CODE="$exit_code" \
    INTRADAY_STATUS_SOURCE="$INTRADAY_SOURCE" \
    INTRADAY_STATUS_TASK_ID="$AQSP_RUN_TASK_ID" \
    INTRADAY_STATUS_MODE="$INTRADAY_MODE" \
    INTRADAY_STATUS_BENCHMARK="$INTRADAY_BENCHMARK_SYMBOL" \
    INTRADAY_STATUS_MAX_UNIVERSE="$INTRADAY_MAX_UNIVERSE" \
    INTRADAY_STATUS_MAX_DATA_LAG="$INTRADAY_MAX_DATA_LAG_DAYS" \
    INTRADAY_STATUS_QUALITY_GATE_SUMMARY="$QUALITY_GATE_SUMMARY" \
    INTRADAY_STATUS_OBSERVATION_ONLY="${OBSERVATION_ONLY:-false}" \
    INTRADAY_STATUS_LEDGER="$INTRADAY_LEDGER" \
    INTRADAY_STATUS_REPORT="$INTRADAY_REPORT" \
    INTRADAY_STATUS_CSV="$INTRADAY_OUTPUT_CSV" \
    INTRADAY_STATUS_NEWS_STATUS="${NEWS_CATALYST_STATUS:-not_run}" \
    INTRADAY_STATUS_NEWS_EXIT_CODE="${NEWS_CATALYST_EXIT_CODE:-0}" \
    INTRADAY_STATUS_NEWS_WARNING="${NEWS_CATALYST_WARNING:-}" \
    INTRADAY_STATUS_NEWS_OUTPUT="$INTRADAY_NEWS_OUTPUT" \
    INTRADAY_STATUS_NEWS_JSON_OUTPUT="$INTRADAY_NEWS_JSON_OUTPUT" \
    "$PYTHON_BIN" - <<'AQSP_INTRADAY_STATUS_PY'
import csv
import json
import os
from pathlib import Path

from aqsp.core.time import now_shanghai


def clean(value: object) -> str:
    return str(value or "").strip()


def truthy(value: object) -> bool:
    return clean(value).lower() in {"1", "true", "yes", "on"}


runtime_metadata: dict[str, str] = {}
csv_path_for_metadata = Path(os.environ["INTRADAY_STATUS_CSV"])
try:
    with csv_path_for_metadata.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if clean(row.get("symbol")) == "__RUN__":
                runtime_metadata = {
                    key: clean(row.get(key))
                    for key in (
                        "run_requested_source",
                        "run_actual_source",
                        "run_source_freshness_tier",
                        "run_source_coverage_tier",
                        "run_source_local_status",
                        "run_fallback_used",
                        "run_data_latest_trade_date",
                        "run_data_lag_days",
                    )
                }
                break
except (OSError, csv.Error):
    runtime_metadata = {}

requested_source = runtime_metadata.get("run_requested_source") or os.environ[
    "INTRADAY_STATUS_SOURCE"
]
actual_source = runtime_metadata.get("run_actual_source", "")
source_freshness_tier = runtime_metadata.get("run_source_freshness_tier", "")
source_coverage_tier = runtime_metadata.get("run_source_coverage_tier", "")
source_local_status = runtime_metadata.get("run_source_local_status", "")
latest_trade_date = runtime_metadata.get("run_data_latest_trade_date", "")
metadata_lag_raw = runtime_metadata.get("run_data_lag_days", "")
try:
    metadata_lag_days = int(float(metadata_lag_raw))
except (TypeError, ValueError):
    metadata_lag_days = -1
provenance_status = "available" if actual_source and source_freshness_tier else "unavailable"

payload = {
    "status": os.environ["INTRADAY_STATUS_VALUE"],
    "task_id": os.environ["INTRADAY_STATUS_TASK_ID"],
    "reason": os.environ["INTRADAY_STATUS_REASON"],
    "exit_code": int(os.environ["INTRADAY_STATUS_EXIT_CODE"]),
    "source": os.environ["INTRADAY_STATUS_SOURCE"],
    "source_provenance": {
        "status": provenance_status,
        "requested_source": requested_source,
        "actual_source": actual_source,
        "source_freshness_tier": source_freshness_tier,
        "source_coverage_tier": source_coverage_tier,
        "source_local_status": source_local_status,
        "fallback_used": truthy(runtime_metadata.get("run_fallback_used")),
        "latest_trade_date": latest_trade_date,
        "lag_days": metadata_lag_days,
    },
    "mode": os.environ["INTRADAY_STATUS_MODE"],
    "benchmark_symbol": os.environ["INTRADAY_STATUS_BENCHMARK"],
    "max_universe": int(os.environ["INTRADAY_STATUS_MAX_UNIVERSE"] or "0"),
    "ledger_path": os.environ["INTRADAY_STATUS_LEDGER"],
    "report_path": os.environ["INTRADAY_STATUS_REPORT"],
    "csv_path": os.environ["INTRADAY_STATUS_CSV"],
    "updated_at": now_shanghai().isoformat(timespec="seconds"),
    "advisory_boundary": "research_only_no_auto_trading",
    "observation_only": os.environ["INTRADAY_STATUS_OBSERVATION_ONLY"].lower()
    in {"1", "true", "yes", "on"},
    "news_catalysts": {
        "status": os.environ["INTRADAY_STATUS_NEWS_STATUS"],
        "exit_code": int(os.environ["INTRADAY_STATUS_NEWS_EXIT_CODE"]),
        "warning": os.environ["INTRADAY_STATUS_NEWS_WARNING"],
        "output": os.environ["INTRADAY_STATUS_NEWS_OUTPUT"],
        "json_output": os.environ["INTRADAY_STATUS_NEWS_JSON_OUTPUT"],
    },
}
quality_gate = {
    "status": "not_run",
    "benchmark_symbol": payload["benchmark_symbol"],
    "freshness_status": "unknown",
    "checked_count": 0,
    "watch_count": 0,
    "blocked_count": 0,
    "downgraded_count": 0,
}
quality_gate_path = Path(os.environ["INTRADAY_STATUS_QUALITY_GATE_SUMMARY"])
try:
    quality_gate.update(json.loads(quality_gate_path.read_text(encoding="utf-8")))
except (OSError, json.JSONDecodeError):
    pass
payload["freshness"] = {
    "status": str(quality_gate.get("freshness_status", "unknown")),
    "lag_days": int(
        quality_gate["lag_days"]
        if quality_gate.get("lag_days") is not None
        else -1
    ),
    "max_allowed_lag_days": int(os.environ["INTRADAY_STATUS_MAX_DATA_LAG"] or "0"),
    "source_freshness_tier": str(quality_gate.get("source_freshness_tier", "") or ""),
    "latest_trade_date": str(
        quality_gate.get("provenance", {}).get("latest_trade_date", "") or ""
    ),
    "provenance_status": str(
        quality_gate.get("provenance_status", "unknown") or "unknown"
    ),
}
payload["quality_gate"] = quality_gate
payload["provenance"] = quality_gate.get("provenance", {})
csv_path = Path(payload["csv_path"])
report_path = Path(payload["report_path"])
candidate_count = 0
actionable_count = 0
focus_count = 0
watch_count = 0
blocked_count = 0
if csv_path.exists() and quality_gate.get("status") != "not_run":
    import csv

    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                symbol = str(row.get("symbol", "") or "").strip()
                if not symbol or symbol == "__RUN__":
                    continue
                candidate_count += 1
                rating = str(row.get("rating", "") or "").strip()
                blocker = str(row.get("candidate_blocker", "") or "").strip()
                status = str(row.get("candidate_status", "") or "").strip()
                quality_action = str(row.get("quality_gate_action", "clean") or "clean").strip()
                blocked = bool(blocker or "阻塞" in status or quality_action == "blocked")
                eligible = quality_action == "clean" and str(
                    row.get("paper_review_eligible", "true") or "true"
                ).lower() == "true"
                if blocked:
                    blocked_count += 1
                elif rating in {"strong_buy_candidate", "buy_candidate"} and eligible:
                    focus_count += 1
                    actionable_count += 1
                else:
                    # Quality-gate observation rows remain visible as watch items.
                    watch_count += 1
    except OSError:
        candidate_count = 0
        actionable_count = 0
        focus_count = 0
        watch_count = 0
        blocked_count = 0
protection_blocked = False
try:
    report_text = report_path.read_text(encoding="utf-8")
    protection_blocked = any(
        marker in report_text
        for marker in ("熔断触发", "组合保护已触发", "组合保护冷却")
    )
except OSError:
    protection_blocked = False
payload.update(
    {
        "csv_exists": csv_path.exists(),
        "candidate_count": candidate_count,
        "actionable_count": actionable_count,
        "paper_review_count": actionable_count,
        "focus_count": focus_count,
        "watch_count": watch_count,
        "blocked_count": blocked_count,
        "protection_blocked": protection_blocked,
        "freshness": payload["freshness"],
        "quality_gate": payload["quality_gate"],
    }
)
path = Path(os.environ["INTRADAY_STATUS_PATH"])
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.replace(path)
AQSP_INTRADAY_STATUS_PY
    log "已写入盘中状态: ${INTRADAY_STATUS}"
}

cleanup() {
    rm -rf "$TMP_DIR" 2>/dev/null || true
    # The initial EXIT trap is replaced below, so remove the lock metadata
    # explicitly before releasing the directory.
    rm -f "$LOCK_INFO_FILE" 2>/dev/null || true
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

cd "$PROJECT_ROOT"

log "=========================================="
log "AI量化选股 - 盘中刷新开始"
log "项目目录: ${PROJECT_ROOT}"
log "Python: ${PYTHON_BIN}"
log "数据源: ${INTRADAY_SOURCE}"
log "市场基准: ${INTRADAY_BENCHMARK_SYMBOL}"
log "=========================================="
write_intraday_status "running" "盘中刷新运行中；候选筛出后会先更新快照" "0"

START_TIME=$(date +%s)
NOTIFY_ARGS=()
if is_truthy "$INTRADAY_ALLOW_NOTIFY" && is_truthy "$INTRADAY_NOTIFY"; then
    NOTIFY_ARGS=(--notify)
elif is_truthy "$INTRADAY_NOTIFY"; then
    log "盘中通知未显式放行，忽略 AQSP_INTRADAY_NOTIFY=true"
fi

set +e
export AQSP_PROVISIONAL_REPORT="${INTRADAY_REPORT}"
export AQSP_PROVISIONAL_OUTPUT_CSV="${INTRADAY_OUTPUT_CSV}"
RUN_CMD=(
    # Without --foreground, GNU timeout owns the command process group and
    # kill-after can converge descendants instead of leaving the task running.
    # Compatibility marker only; do not restore the old foreground invocation:
    # timeout --foreground --signal=TERM --kill-after=15s "${INTRADAY_RUN_TIMEOUT_SECONDS}s"
    timeout --signal=TERM --kill-after=15s "${INTRADAY_RUN_TIMEOUT_SECONDS}s"
    "${PYTHON_BIN}" -m aqsp run
    --source "${INTRADAY_SOURCE}"
    --mode "${INTRADAY_MODE}"
    --limit "${INTRADAY_LIMIT}"
    --max-universe "${INTRADAY_MAX_UNIVERSE}"
    --min-avg-amount "${INTRADAY_MIN_AVG_AMOUNT}"
    --max-data-lag-days "${INTRADAY_MAX_DATA_LAG_DAYS}"
    --benchmark-symbol "${INTRADAY_BENCHMARK_SYMBOL}"
    --ledger "${TMP_INTRADAY_LEDGER}"
    --report "${TMP_INTRADAY_REPORT}"
    --output-csv "${TMP_INTRADAY_OUTPUT_CSV}"
    --skip-validation
)
if [ "${#NOTIFY_ARGS[@]}" -gt 0 ]; then
    RUN_CMD+=("${NOTIFY_ARGS[@]}")
fi
"${RUN_CMD[@]}" 2>&1 | tee -a "$RESULT_LOG"
RUN_PIPE_STATUS=("${PIPESTATUS[@]}")
RUN_EXIT_CODE="${RUN_PIPE_STATUS[0]:-1}"
TEE_EXIT_CODE="${RUN_PIPE_STATUS[1]:-1}"
unset AQSP_PROVISIONAL_REPORT AQSP_PROVISIONAL_OUTPUT_CSV
set -e

PARTIAL_SNAPSHOT_USED="false"
SCRIPT_EXIT_CODE="0"
OBSERVATION_ONLY="false"
OBSERVATION_REASON=""
RUN_TIMED_OUT="false"
if [ "$RUN_EXIT_CODE" -eq 124 ] || [ "$RUN_EXIT_CODE" -eq 137 ] || [ "$RUN_EXIT_CODE" -eq 143 ]; then
    RUN_TIMED_OUT="true"
    log "[ERROR] 盘中任务达到 timeout=${INTRADAY_RUN_TIMEOUT_SECONDS}s，子进程组已请求收敛；命令退出码: ${RUN_EXIT_CODE}"
fi
if [ "$TEE_EXIT_CODE" -ne 0 ]; then
    log "[WARN] 盘中结果日志管道退出码: ${TEE_EXIT_CODE}"
fi
if [ "$RUN_EXIT_CODE" -ne 0 ] && [ "$RUN_EXIT_CODE" -ne 2 ]; then
    if [ -f "$TMP_INTRADAY_OUTPUT_CSV" ] || [ -f "$TMP_INTRADAY_REPORT" ]; then
        log "[WARN] 盘中后处理未完整结束，退出码: ${RUN_EXIT_CODE}；使用已落盘的盘中快照刷新展示"
        write_intraday_status "partial_failed" "盘中快照已生成；后处理失败，保留快照但生产状态需复核" "$RUN_EXIT_CODE"
        SCRIPT_EXIT_CODE="$RUN_EXIT_CODE"
        PARTIAL_SNAPSHOT_USED="true"
    else
        log "[ERROR] 盘中选股失败，退出码: ${RUN_EXIT_CODE}"
        write_intraday_status "failed" "盘中选股失败，保留上一版盘中产物" "$RUN_EXIT_CODE"
        exit "$RUN_EXIT_CODE"
    fi
fi

QUALITY_GATE_EXIT_CODE="0"
if apply_intraday_quality_gate "$TMP_INTRADAY_OUTPUT_CSV" "$TMP_INTRADAY_LEDGER" "$TMP_INTRADAY_REPORT" "$QUALITY_GATE_SUMMARY"; then
    QUALITY_GATE_EXIT_CODE="0"
else
    QUALITY_GATE_EXIT_CODE="$?"
fi

if [ "$QUALITY_GATE_EXIT_CODE" -ne 0 ]; then
    OBSERVATION_REASON="质量门未通过，但可用性和新鲜度仍允许观察展示"
    if promote_observation_only_if_safe; then
        OBSERVATION_ONLY="true"
        SCRIPT_EXIT_CODE="1"
        log "[WARN] 质量门失败但临时产物可用且新鲜，已晋级 observation-only；不进入推荐或正式 ledger"
    elif promote_provisional_observation_only; then
        SCRIPT_EXIT_CODE="1"
        log "[WARN] 质量门失败且仅有最终临时 CSV，已安全标记 observation-only；不进入推荐或正式 ledger"
    else
        log "[ERROR] 盘中质量门未完成，保留上一版盘中产物"
        write_intraday_status "failed" "盘中质量门失败，保留上一版盘中产物" "1"
        exit 1
    fi
fi

if [ "$PARTIAL_SNAPSHOT_USED" = "true" ] && [ "$OBSERVATION_ONLY" != "true" ]; then
    OBSERVATION_REASON="盘中后处理未完整结束，仅保留观察展示"
    if promote_observation_only_if_safe; then
        OBSERVATION_ONLY="true"
        log "[WARN] 盘中后处理未完整结束，已将新鲜临时产物降级为 observation-only"
    elif promote_provisional_observation_only; then
        log "[WARN] 盘中后处理未完整结束，已将最终临时 CSV 降级为 observation-only"
    else
        log "[ERROR] 部分盘中产物未通过可用性/新鲜度观察晋级，保留上一版盘中产物"
        write_intraday_status "failed" "盘中后处理产物未通过可用性或新鲜度判断" "$RUN_EXIT_CODE"
        exit "$RUN_EXIT_CODE"
    fi
fi

if [ "$RUN_EXIT_CODE" -eq 2 ]; then
    log "盘中选股触发熔断，仅刷新观察展示，不新增正式待复核"
    if [ "$OBSERVATION_ONLY" != "true" ]; then
        OBSERVATION_REASON="盘中选股触发熔断，仅保留观察展示"
        if promote_observation_only_if_safe; then
            OBSERVATION_ONLY="true"
        else
            log "[ERROR] 熔断产物未通过可用性/新鲜度观察晋级，保留上一版盘中产物"
            write_intraday_status "failed" "盘中熔断产物未通过可用性或新鲜度质量判断" "1"
            exit 1
        fi
    fi
fi

if [ "$OBSERVATION_ONLY" != "true" ] && quality_gate_requires_observation; then
    OBSERVATION_REASON="质量门状态为 blocked，仅保留观察展示"
    if promote_observation_only_if_safe; then
        OBSERVATION_ONLY="true"
        log "[WARN] 质量门状态为 blocked，已将完整新鲜快照降级为 observation-only"
    else
        log "[ERROR] blocked 质量门产物未通过可用性/新鲜度观察晋级，保留上一版盘中产物"
        write_intraday_status "failed" "blocked 质量门产物未通过可用性或新鲜度判断" "1"
        exit 1
    fi
fi

if [ "$OBSERVATION_ONLY" = "true" ]; then
    if [ "$QUALITY_GATE_EXIT_CODE" -ne 0 ] || \
       [ "$RUN_TIMED_OUT" = "true" ] || \
       [ "$PARTIAL_SNAPSHOT_USED" = "true" ]; then
        write_intraday_status "partial_failed" "盘中任务未完整完成；最新产物已晋级 observation-only，不作推荐或正式 ledger 输入" "$SCRIPT_EXIT_CODE"
    else
        write_intraday_status "observation_only" "盘中最新产物已晋级 observation-only；不作推荐或正式 ledger 输入" "$SCRIPT_EXIT_CODE"
    fi
elif ! is_truthy "$PARTIAL_SNAPSHOT_USED"; then
    replace_intraday_artifact "$TMP_INTRADAY_LEDGER" "$INTRADAY_LEDGER" "盘中 ledger"
    replace_intraday_artifact "$TMP_INTRADAY_REPORT" "$INTRADAY_REPORT" "盘中报告"
    replace_intraday_artifact "$TMP_INTRADAY_OUTPUT_CSV" "$INTRADAY_OUTPUT_CSV" "盘中 CSV"
    write_intraday_status "completed" "盘中刷新完成；保护状态仅提示，不重写候选队列" "$RUN_EXIT_CODE"
fi

# Publish fresh candidates before the slower news/debate sidecars. The final
# refresh below merges those advisory layers without delaying the live board.
if refresh_home_dashboard_snapshot; then
    log "盘中候选首页已先行刷新；消息与讨论完成后会再次合并"
else
    SCRIPT_EXIT_CODE="1"
    write_intraday_status "partial_failed" "盘中候选已刷新，但首页先行刷新失败" "$SCRIPT_EXIT_CODE"
fi

refresh_realtime_cross_market_context
refresh_intraday_news_catalysts
if [ "$NEWS_CATALYST_STATUS" = "warning" ]; then
    # News is advisory-only. Preserve its warning in the status artifact, but
    # do not turn a fresh candidate/home publish into a failed scheduled run.
    write_intraday_status "partial_failed" "盘中候选已刷新；消息面仅标记 warning，继续首页快照" "$SCRIPT_EXIT_CODE"
elif [ "$OBSERVATION_ONLY" = "true" ]; then
    if [ "$QUALITY_GATE_EXIT_CODE" -ne 0 ] || [ "$RUN_TIMED_OUT" = "true" ] || [ "$PARTIAL_SNAPSHOT_USED" = "true" ]; then
        write_intraday_status "partial_failed" "盘中任务未完整完成；消息面已处理，最新产物仍为 observation-only" "$SCRIPT_EXIT_CODE"
    else
        write_intraday_status "observation_only" "盘中最新产物为 observation-only；消息面已处理" "$SCRIPT_EXIT_CODE"
    fi
elif ! is_truthy "$PARTIAL_SNAPSHOT_USED"; then
    write_intraday_status "completed" "盘中刷新完成；候选与消息面已更新" "$SCRIPT_EXIT_CODE"
else
    write_intraday_status "partial_failed" "盘中候选已刷新；后处理仍需复核，消息面已处理" "$SCRIPT_EXIT_CODE"
fi

if [ "$OBSERVATION_ONLY" = "true" ]; then
    log "盘中产物为 observation-only，跳过 Agent 回填，避免观察内容被误读为推荐"
else
    launch_intraday_debate_backfill
fi
if ! refresh_home_dashboard_snapshot; then
    SCRIPT_EXIT_CODE="1"
    write_intraday_status "partial_failed" "盘中数据已刷新，但首页快照刷新失败，继续保留上一版首页" "$SCRIPT_EXIT_CODE"
fi

"${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/open_dashboard.py" \
    --render-only \
    --no-open-browser \
    --csv "${INTRADAY_OUTPUT_CSV}" \
    --ledger "${INTRADAY_LEDGER}" \
    --paper-ledger "${PAPER_LEDGER}" \
    --output "${INTRADAY_DASHBOARD_HTML}" \
    --db "${INTRADAY_DASHBOARD_DB}" 2>&1 | tee -a "$RESULT_LOG"

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

log "=========================================="
log "盘中刷新结束"
log "退出码: ${RUN_EXIT_CODE}"
log "耗时: ${DURATION}秒"
log "Dashboard: ${INTRADAY_DASHBOARD_HTML}"
log "报告: ${INTRADAY_REPORT}"
log "CSV: ${INTRADAY_OUTPUT_CSV}"
log "=========================================="

find "$LOG_DIR" -name "intraday-*.log" -mtime +15 -delete 2>/dev/null || true

exit "$SCRIPT_EXIT_CODE"
