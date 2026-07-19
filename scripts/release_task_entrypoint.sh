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

runtime_path() {
    case "${1:-}" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$RUNTIME_ROOT" "${1:-}" ;;
    esac
}

export AQSP_PROJECT_ROOT="$RELEASE_ROOT"
export AQSP_IMMUTABLE_RELEASE="${AQSP_IMMUTABLE_RELEASE:-true}"
export AQSP_RUNTIME_VENV_DIR="${AQSP_RUNTIME_VENV_DIR:-${RELEASE_ROOT}/.venv-vibe-research}"
export AQSP_LEDGER="$(runtime_path "${AQSP_LEDGER:-data/predictions.jsonl}")"
export AQSP_PAPER_LEDGER="$(runtime_path "${AQSP_PAPER_LEDGER:-data/paper_trades.jsonl}")"
export AQSP_DEBATE_RESULTS="$(runtime_path "${AQSP_DEBATE_RESULTS:-data/debate_results.jsonl}")"
export AQSP_INTRADAY_LEDGER="$(runtime_path "${AQSP_INTRADAY_LEDGER:-data/intraday_predictions.jsonl}")"
export AQSP_INTRADAY_LATEST_CSV="$(runtime_path "${AQSP_INTRADAY_LATEST_CSV:-reports/intraday_latest.csv}")"
export AQSP_OUTPUT_CSV="$(runtime_path "${AQSP_OUTPUT_CSV:-reports/latest.csv}")"
export AQSP_NEWS_OUTPUT="$(runtime_path "${AQSP_NEWS_OUTPUT:-reports/news_catalysts.md}")"
export AQSP_NEWS_JSON_OUTPUT="$(runtime_path "${AQSP_NEWS_JSON_OUTPUT:-data/runtime/news_catalysts_latest.json}")"
export AQSP_NEWS_ARCHIVE_DIR="$(runtime_path "${AQSP_NEWS_ARCHIVE_DIR:-data/runtime/news_archive}")"
export AQSP_NEWS_SOURCE_CONFIG="${AQSP_NEWS_SOURCE_CONFIG:-${RELEASE_ROOT}/config/news_sources.yaml}"
export AQSP_BT_LOGS_DIR="$(runtime_path "${AQSP_BT_LOGS_DIR:-logs/bt}")"
export AQSP_RISK_STATE="$(runtime_path "${AQSP_RISK_STATE:-data/risk_state.json}")"
export AQSP_WALKFORWARD_GATE_PATH="$(runtime_path "${AQSP_WALKFORWARD_GATE_PATH:-data/walkforward_gate.json}")"
export AQSP_WALKFORWARD_PRODUCTION_STATUS="$(runtime_path "${AQSP_WALKFORWARD_PRODUCTION_STATUS:-data/walkforward_production_status.json}")"
export AQSP_GATE_NOTIFY_STATE_PATH="$(runtime_path "${AQSP_GATE_NOTIFY_STATE_PATH:-data/gate_notify_state.json}")"
export AQSP_REALTIME_CROSS_MARKET_PATH="$(runtime_path "${AQSP_REALTIME_CROSS_MARKET_PATH:-data/runtime/realtime_cross_market_context.json}")"

exec /bin/bash "${RELEASE_ROOT}/scripts/bt_task.sh" "$@"