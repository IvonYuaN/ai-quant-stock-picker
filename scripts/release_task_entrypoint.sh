#!/usr/bin/env bash
# Run scheduled AQSP work from an immutable release while keeping runtime data private.
set -euo pipefail

RELEASE_ROOT="${AQSP_RELEASE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-/opt/aqsp}"

if [[ -f "${RUNTIME_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${RUNTIME_ROOT}/.env"
    set +a
fi

export AQSP_PROJECT_ROOT="$RELEASE_ROOT"
export AQSP_IMMUTABLE_RELEASE="${AQSP_IMMUTABLE_RELEASE:-true}"
export AQSP_RUNTIME_VENV_DIR="${AQSP_RUNTIME_VENV_DIR:-${RELEASE_ROOT}/.venv-vibe-research}"
export AQSP_LEDGER="${AQSP_LEDGER:-${RUNTIME_ROOT}/data/predictions.jsonl}"
export AQSP_PAPER_LEDGER="${AQSP_PAPER_LEDGER:-${RUNTIME_ROOT}/data/paper_trades.jsonl}"
export AQSP_DEBATE_RESULTS="${AQSP_DEBATE_RESULTS:-${RUNTIME_ROOT}/data/debate_results.jsonl}"
export AQSP_INTRADAY_LEDGER="${AQSP_INTRADAY_LEDGER:-${RUNTIME_ROOT}/data/intraday_predictions.jsonl}"
export AQSP_INTRADAY_LATEST_CSV="${AQSP_INTRADAY_LATEST_CSV:-${RUNTIME_ROOT}/reports/intraday_latest.csv}"
export AQSP_OUTPUT_CSV="${AQSP_OUTPUT_CSV:-${RUNTIME_ROOT}/reports/latest.csv}"
export AQSP_NEWS_OUTPUT="${AQSP_NEWS_OUTPUT:-${RUNTIME_ROOT}/reports/news_catalysts.md}"
export AQSP_NEWS_JSON_OUTPUT="${AQSP_NEWS_JSON_OUTPUT:-${RUNTIME_ROOT}/data/runtime/news_catalysts_latest.json}"
export AQSP_NEWS_ARCHIVE_DIR="${AQSP_NEWS_ARCHIVE_DIR:-${RUNTIME_ROOT}/data/runtime/news_archive}"
export AQSP_NEWS_SOURCE_CONFIG="${AQSP_NEWS_SOURCE_CONFIG:-${RELEASE_ROOT}/config/news_sources.yaml}"
export AQSP_BT_LOGS_DIR="${AQSP_BT_LOGS_DIR:-${RUNTIME_ROOT}/logs/bt}"
export AQSP_RISK_STATE="${AQSP_RISK_STATE:-${RUNTIME_ROOT}/data/risk_state.json}"
export AQSP_WALKFORWARD_GATE_PATH="${AQSP_WALKFORWARD_GATE_PATH:-${RUNTIME_ROOT}/data/walkforward_gate.json}"
export AQSP_WALKFORWARD_PRODUCTION_STATUS="${AQSP_WALKFORWARD_PRODUCTION_STATUS:-${RUNTIME_ROOT}/data/walkforward_production_status.json}"
export AQSP_GATE_NOTIFY_STATE_PATH="${AQSP_GATE_NOTIFY_STATE_PATH:-${RUNTIME_ROOT}/data/gate_notify_state.json}"
export AQSP_REALTIME_CROSS_MARKET_PATH="${AQSP_REALTIME_CROSS_MARKET_PATH:-${RUNTIME_ROOT}/data/runtime/realtime_cross_market_context.json}"

exec /bin/bash "${RELEASE_ROOT}/scripts/bt_task.sh" "$@"
