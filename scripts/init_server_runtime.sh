#!/usr/bin/env bash
# 初始化服务器运行态文件：
# 1. 创建关键目录
# 2. 初始化 paper ledger / risk state
# 3. 输出当前文件状态

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
fi

resolve_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$PROJECT_ROOT" "$1" ;;
    esac
}

PAPER_LEDGER="$(resolve_path "${AQSP_PAPER_LEDGER:-data/paper_trades.jsonl}")"
RISK_STATE="$(resolve_path "${AQSP_RISK_STATE:-data/risk_state.json}")"
INTRADAY_LEDGER="$(resolve_path "${AQSP_INTRADAY_LEDGER:-data/intraday_predictions.jsonl}")"
REPORT_PATH="$(resolve_path "${AQSP_REPORT:-reports/latest.md}")"
CSV_PATH="$(resolve_path "${AQSP_OUTPUT_CSV:-reports/latest.csv}")"
INTRADAY_REPORT="$(resolve_path "${AQSP_INTRADAY_REPORT:-reports/intraday_latest.md}")"
INTRADAY_CSV="$(resolve_path "${AQSP_INTRADAY_OUTPUT_CSV:-reports/intraday_latest.csv}")"
DASHBOARD_HTML="$(resolve_path "${AQSP_DASHBOARD_HTML:-dist/dashboard/index.html}")"
DASHBOARD_DB="$(resolve_path "${AQSP_DASHBOARD_DB:-dist/dashboard/aqsp.db}")"

mkdir -p \
    "${PROJECT_ROOT}/data" \
    "${PROJECT_ROOT}/reports" \
    "${PROJECT_ROOT}/dist/dashboard" \
    "${PROJECT_ROOT}/logs" \
    "${PROJECT_ROOT}/logs/daily" \
    "${PROJECT_ROOT}/logs/deploy" \
    "${PROJECT_ROOT}/logs/intraday" \
    "$(dirname "$PAPER_LEDGER")" \
    "$(dirname "$RISK_STATE")" \
    "$(dirname "$INTRADAY_LEDGER")" \
    "$(dirname "$REPORT_PATH")" \
    "$(dirname "$CSV_PATH")" \
    "$(dirname "$INTRADAY_REPORT")" \
    "$(dirname "$INTRADAY_CSV")" \
    "$(dirname "$DASHBOARD_HTML")" \
    "$(dirname "$DASHBOARD_DB")"

if [ ! -f "$PAPER_LEDGER" ]; then
    : > "$PAPER_LEDGER"
fi

if [ ! -f "$INTRADAY_LEDGER" ]; then
    : > "$INTRADAY_LEDGER"
fi

if [ ! -f "$RISK_STATE" ]; then
    cat >"$RISK_STATE" <<'EOF'
{
  "cooldown_until": null,
  "last_triggered_date": null
}
EOF
fi

echo "paper_ledger=$PAPER_LEDGER"
ls -lh "$PAPER_LEDGER"
echo "risk_state=$RISK_STATE"
ls -lh "$RISK_STATE"
echo "intraday_ledger=$INTRADAY_LEDGER"
ls -lh "$INTRADAY_LEDGER"
