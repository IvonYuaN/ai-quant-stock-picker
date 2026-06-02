#!/usr/bin/env bash
# 服务器状态总览：
# 1. Git 状态
# 2. Cron 任务
# 3. 关键产物
# 4. 最新日志

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"

print_section() {
    printf '\n===== %s =====\n' "$1"
}

file_line() {
    local path="$1"
    if [ -e "$path" ]; then
        ls -lh "$path"
    else
        printf 'missing %s\n' "$path"
    fi
}

print_section "GIT"
cd "$PROJECT_ROOT"
git log --oneline -3
git status --short

print_section "CRON"
crontab -l 2>/dev/null || true

print_section "ARTIFACTS"
file_line "${PROJECT_ROOT}/reports/latest.md"
file_line "${PROJECT_ROOT}/reports/latest.csv"
file_line "${PROJECT_ROOT}/reports/briefing.md"
file_line "${PROJECT_ROOT}/reports/intraday_latest.md"
file_line "${PROJECT_ROOT}/reports/intraday_latest.csv"
file_line "${PROJECT_ROOT}/dist/dashboard/index.html"
file_line "${PROJECT_ROOT}/dist/dashboard/aqsp.db"

print_section "RUNTIME"
if [ -f "${PROJECT_ROOT}/.venv/bin/python3" ] && [ -f "${PROJECT_ROOT}/scripts/diagnose_runtime.py" ]; then
    "${PROJECT_ROOT}/.venv/bin/python3" "${PROJECT_ROOT}/scripts/diagnose_runtime.py" || true
else
    echo "diagnose_runtime unavailable"
fi

print_section "DEPLOY LOG"
tail -n 40 "${PROJECT_ROOT}/logs/deploy/sync-$(date +%Y-%m-%d).log" 2>/dev/null || true

print_section "INTRADAY LOG"
tail -n 40 "${PROJECT_ROOT}/logs/intraday/intraday-$(date +%Y-%m-%d).log" 2>/dev/null || true

print_section "DAILY LOG"
tail -n 40 "${PROJECT_ROOT}/logs/daily/pipeline-$(date +%Y-%m-%d).log" 2>/dev/null || true
