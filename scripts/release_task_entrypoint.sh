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
    local raw relative candidate
    case "${1:-}" in
        /*)
            candidate="$1"
            ;;
        *)
            relative="${1:-}"
            relative="${relative#data/}"
            candidate="${RUNTIME_DATA_ROOT}/${relative}"
            ;;
    esac
    raw="$("${AQSP_BOOTSTRAP_PYTHON:-python3}" - "$RUNTIME_DATA_ROOT" "$candidate" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).expanduser().resolve(strict=False)
candidate = Path(sys.argv[2]).expanduser().resolve(strict=False)
try:
    candidate.relative_to(root)
except ValueError:
    raise SystemExit(1)
print(candidate)
PY
    )" || {
        echo "runtime output must be under ${RUNTIME_DATA_ROOT}: ${1:-}" >&2
        exit 1
    }
    printf '%s\n' "$raw"
}

export_runtime_path() {
    local name="$1" default="$2" raw resolved
    raw="${!name-}"
    if [[ -z "$raw" ]]; then
        raw="$default"
    fi
    resolved="$(runtime_path "$raw")"
    export "$name=$resolved"
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
export AQSP_RUNTIME_DATA_ROOT="$RUNTIME_DATA_ROOT"
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
# Releases are immutable and do not carry a venv. A legacy runtime .env may
# still name a removed release-local interpreter, so reject that path before
# the scheduler starts and use the shared server venv instead.
SHARED_VENV_DIR="${AQSP_SHARED_VENV_DIR:-/opt/aqsp-vibe-venv}"
case "${AQSP_RUNTIME_PYTHON:-}" in
    ""|"${RELEASE_ROOT}"/*)
        export AQSP_RUNTIME_VENV_DIR="$SHARED_VENV_DIR"
        export AQSP_RUNTIME_PYTHON="${SHARED_VENV_DIR}/bin/python3"
        ;;
    *)
        export AQSP_RUNTIME_VENV_DIR="${AQSP_RUNTIME_VENV_DIR:-$SHARED_VENV_DIR}"
        ;;
esac
# This entrypoint is only for releases; a legacy .env must not re-enable Git
# sync against the mutable staging checkout.
export AQSP_IMMUTABLE_RELEASE=true
export_runtime_path AQSP_LEDGER data/predictions.jsonl
export_runtime_path AQSP_PAPER_LEDGER data/paper_trades.jsonl
export_runtime_path AQSP_DEBATE_RESULTS data/debate_results.jsonl
export_runtime_path AQSP_INTRADAY_LEDGER data/intraday_predictions.jsonl
export_runtime_path AQSP_REPORT reports/latest.md
export_runtime_path AQSP_OUTPUT_CSV reports/latest.csv
export_runtime_path AQSP_INTRADAY_REPORT reports/intraday_latest.md
export_runtime_path AQSP_INTRADAY_LATEST_CSV reports/intraday_latest.csv
export_runtime_path AQSP_INTRADAY_OUTPUT_CSV reports/intraday_latest.csv
export_runtime_path AQSP_INTRADAY_STATUS data/intraday_refresh_status.json
export AQSP_INTRADAY_REFRESH_STATUS_PATH="$AQSP_INTRADAY_STATUS"
export_runtime_path AQSP_INTRADAY_CURSOR_PATH data/runtime/intraday_universe_cursor.json
# React + FastAPI is the public surface. Offline archives stay private runtime data.
export_runtime_path AQSP_DASHBOARD_HTML data/runtime/archive/dashboard/index.html
export_runtime_path AQSP_DASHBOARD_DB data/runtime/archive/dashboard/aqsp.db
export_runtime_path AQSP_HOME_SNAPSHOT_PATH data/runtime/home_dashboard_snapshot.json
export_runtime_path AQSP_HOME_SNAPSHOT_INDEX_PATH data/runtime/home_dashboard_snapshot_index.json
export_runtime_path AQSP_VARIANT_RESULTS data/runtime/variant_results.json
export_runtime_path AQSP_NEWS_OUTPUT reports/news_catalysts.md
export_runtime_path AQSP_NEWS_JSON_OUTPUT data/runtime/news_catalysts_latest.json
export_runtime_path AQSP_NEWS_ARCHIVE_DIR data/runtime/news_archive
export AQSP_NEWS_SOURCE_CONFIG="${AQSP_NEWS_SOURCE_CONFIG:-${RELEASE_ROOT}/config/news_sources.yaml}"
export_runtime_path AQSP_BT_LOGS_DIR logs/bt
export_runtime_path AQSP_DAILY_LOG_DIR logs/daily
export_runtime_path AQSP_MIDDAY_LOG_DIR logs/midday
export_runtime_path AQSP_PIPELINE_LOG_DIR logs/pipeline
export_runtime_path AQSP_DAILY_RUN_HISTORY data/daily_run_history.jsonl
export_runtime_path AQSP_CATALYST_REPORT_CACHE_PATH data/runtime/catalyst_report_cache.json
export_runtime_path AQSP_RUNTIME_LOCK_DIR .locks
export_runtime_path AQSP_RUNTIME_STATE_DIR .state
export_runtime_path AQSP_RUNTIME_TMP_ROOT .tmp
export_runtime_path AQSP_DEPLOY_LOG_DIR logs/deploy
export_runtime_path AQSP_MONITOR_LOG_DIR logs/monitor
export_runtime_path AQSP_NEWS_LOG_DIR logs/news
export_runtime_path AQSP_COLDSTART_LOG_DIR logs/coldstart
export_runtime_path AQSP_COLDSTART_HANDOFF_STATUS_PATH data/coldstart_handoff_status.json
export_runtime_path AQSP_COLDSTART_REPORT outputs/coldstart_latest.md
export_runtime_path AQSP_COLDSTART_OUTPUT_CSV outputs/coldstart_latest.csv
export_runtime_path AQSP_RISK_STATE data/risk_state.json
export_runtime_path AQSP_WALKFORWARD_GATE_PATH data/walkforward_gate.json
export_runtime_path AQSP_WALKFORWARD_PRODUCTION_STATUS data/walkforward_production_status.json
export_runtime_path AQSP_GATE_NOTIFY_STATE_PATH data/gate_notify_state.json
export_runtime_path AQSP_REALTIME_CROSS_MARKET_PATH data/runtime/realtime_cross_market_context.json
export_runtime_path AQSP_RUNTIME_SYMBOL_CACHE data/walkforward_production_symbols.json
export_runtime_path AQSP_INTRADAY_FAST_SYMBOL_CACHE data/walkforward_production_symbols.json
if [[ -z "${AQSP_INTRADAY_FAST_SYMBOL_CSVS:-}" ]]; then
    export AQSP_INTRADAY_FAST_SYMBOL_CSVS="$(runtime_path reports/intraday_latest.csv),$(runtime_path reports/latest.csv)"
fi

exec /bin/bash "${RELEASE_ROOT}/scripts/bt_task.sh" "$@"
