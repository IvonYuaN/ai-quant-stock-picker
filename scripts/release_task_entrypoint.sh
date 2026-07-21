#!/usr/bin/env bash
# Run scheduled AQSP work from an immutable release while keeping runtime data private.
set -euo pipefail

RELEASE_ROOT="${AQSP_RELEASE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-/opt/aqsp}"
RUNTIME_DATA_ROOT="${AQSP_RUNTIME_DATA_ROOT:-${RUNTIME_ROOT}/data}"

if [[ -f "${RUNTIME_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${RUNTIME_ROOT}/.env"
    set +a
fi

runtime_path() {
    case "${1:-}" in
        /*)
            case "$1" in
                "$RUNTIME_DATA_ROOT"|"$RUNTIME_DATA_ROOT"/*) printf '%s\n' "$1" ;;
                *) echo "runtime output must be under ${RUNTIME_DATA_ROOT}: $1" >&2; exit 1 ;;
            esac
            ;;
        *)
            relative="${1:-}"
            relative="${relative#data/}"
            printf '%s/%s\n' "$RUNTIME_DATA_ROOT" "$relative"
            ;;
    esac
}

case "$RUNTIME_DATA_ROOT" in
    /*) ;;
    *) echo "AQSP_RUNTIME_DATA_ROOT must be absolute: $RUNTIME_DATA_ROOT" >&2; exit 1 ;;
esac
case "$RELEASE_ROOT" in
    "$RUNTIME_DATA_ROOT"|"$RUNTIME_DATA_ROOT"/*) echo "runtime data cannot be inside release: $RUNTIME_DATA_ROOT" >&2; exit 1 ;;
esac
case "$RUNTIME_DATA_ROOT" in
    "$RELEASE_ROOT"|"$RELEASE_ROOT"/*) echo "runtime data cannot be inside release: $RUNTIME_DATA_ROOT" >&2; exit 1 ;;
esac

export AQSP_PROJECT_ROOT="$RELEASE_ROOT"
export AQSP_RUNTIME_ROOT="$RUNTIME_ROOT"
export AQSP_IMMUTABLE_RELEASE="${AQSP_IMMUTABLE_RELEASE:-true}"
export AQSP_RELEASE_MANIFEST="${AQSP_RELEASE_MANIFEST:-${RELEASE_ROOT}/.aqsp-release.json}"
if [[ -f "$AQSP_RELEASE_MANIFEST" ]]; then
    AQSP_RELEASE_COMMIT="$(${AQSP_RUNTIME_PYTHON:-python3} - "$AQSP_RELEASE_MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
commit = payload.get("commit")
if not isinstance(commit, str) or len(commit) != 40:
    raise SystemExit("invalid release manifest commit")
print(commit)
PY
    )"
    export AQSP_RELEASE_COMMIT
else
    echo "[WARN] release identity manifest missing: ${AQSP_RELEASE_MANIFEST}" >&2
fi
export AQSP_RUNTIME_VENV_DIR="${AQSP_RUNTIME_VENV_DIR:-${RELEASE_ROOT}/.venv-vibe-research}"
export AQSP_LEDGER="$(runtime_path "${AQSP_LEDGER:-data/predictions.jsonl}")"
export AQSP_PAPER_LEDGER="$(runtime_path "${AQSP_PAPER_LEDGER:-data/paper_trades.jsonl}")"
export AQSP_DEBATE_RESULTS="$(runtime_path "${AQSP_DEBATE_RESULTS:-data/debate_results.jsonl}")"
export AQSP_INTRADAY_LEDGER="$(runtime_path "${AQSP_INTRADAY_LEDGER:-data/intraday_predictions.jsonl}")"
export AQSP_REPORT="$(runtime_path "${AQSP_REPORT:-reports/latest.md}")"
export AQSP_OUTPUT_CSV="$(runtime_path "${AQSP_OUTPUT_CSV:-reports/latest.csv}")"
export AQSP_INTRADAY_REPORT="$(runtime_path "${AQSP_INTRADAY_REPORT:-reports/intraday_latest.md}")"
export AQSP_INTRADAY_LATEST_CSV="$(runtime_path "${AQSP_INTRADAY_LATEST_CSV:-reports/intraday_latest.csv}")"
export AQSP_INTRADAY_OUTPUT_CSV="$(runtime_path "${AQSP_INTRADAY_OUTPUT_CSV:-reports/intraday_latest.csv}")"
export AQSP_INTRADAY_STATUS="$(runtime_path "${AQSP_INTRADAY_STATUS:-data/intraday_refresh_status.json}")"
export AQSP_INTRADAY_REFRESH_STATUS_PATH="$AQSP_INTRADAY_STATUS"
export AQSP_INTRADAY_CURSOR_PATH="$(runtime_path "${AQSP_INTRADAY_CURSOR_PATH:-data/runtime/intraday_universe_cursor.json}")"
export AQSP_DASHBOARD_HTML="$(runtime_path "${AQSP_DASHBOARD_HTML:-dist/dashboard/index.html}")"
export AQSP_DASHBOARD_DB="$(runtime_path "${AQSP_DASHBOARD_DB:-dist/dashboard/aqsp.db}")"
export AQSP_HOME_SNAPSHOT_PATH="$(runtime_path "${AQSP_HOME_SNAPSHOT_PATH:-data/runtime/home_dashboard_snapshot.json}")"
export AQSP_HOME_SNAPSHOT_INDEX_PATH="$(runtime_path "${AQSP_HOME_SNAPSHOT_INDEX_PATH:-data/runtime/home_dashboard_snapshot_index.json}")"
export AQSP_VARIANT_RESULTS="$(runtime_path "${AQSP_VARIANT_RESULTS:-data/runtime/variant_results.json}")"
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
export AQSP_RUNTIME_SYMBOL_CACHE="$(runtime_path "${AQSP_RUNTIME_SYMBOL_CACHE:-data/walkforward_production_symbols.json}")"
export AQSP_INTRADAY_FAST_SYMBOL_CACHE="$(runtime_path "${AQSP_INTRADAY_FAST_SYMBOL_CACHE:-data/walkforward_production_symbols.json}")"
if [[ -z "${AQSP_INTRADAY_FAST_SYMBOL_CSVS:-}" ]]; then
    export AQSP_INTRADAY_FAST_SYMBOL_CSVS="$(runtime_path reports/intraday_latest.csv),$(runtime_path reports/latest.csv)"
fi

exec /bin/bash "${RELEASE_ROOT}/scripts/bt_task.sh" "$@"
