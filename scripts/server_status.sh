#!/usr/bin/env bash
# 服务器状态总览：
# 1. Git 状态
# 2. Cron 任务
# 3. 关键产物
# 4. 最新日志

set -euo pipefail

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
LOCK_STALE_MINUTES="${AQSP_LOCK_STALE_MINUTES:-360}"
RUNNER_TIMEOUT_SECONDS="${AQSP_RUNNER_TIMEOUT_SECONDS:-0}"
MONITOR_TIMEOUT_SECONDS="${AQSP_MONITOR_TIMEOUT_SECONDS:-0}"

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
fi

print_section() {
    printf '\n===== %s =====\n' "$1"
}

lock_age_minutes() {
    local path="$1"
    local now_epoch mtime
    now_epoch="$(date +%s)"
    mtime="$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path")"
    echo $(( (now_epoch - mtime) / 60 ))
}

print_lock_state() {
    local lock_path="$1"
    local lock_name info_file runner pid started_at age pid_state
    lock_name="$(basename "$lock_path")"
    if [ ! -d "$lock_path" ]; then
        printf '%s missing\n' "$lock_name"
        return
    fi

    info_file="${lock_path}/meta.env"
    runner="unknown"
    pid="unknown"
    started_at="unknown"
    if [ -f "$info_file" ]; then
        while IFS='=' read -r key value; do
            case "$key" in
                LOCK_RUNNER) [ -n "$value" ] && runner="$value" ;;
                LOCK_PID) [ -n "$value" ] && pid="$value" ;;
                LOCK_STARTED_AT) [ -n "$value" ] && started_at="$value" ;;
            esac
        done <"$info_file"
    fi

    age="$(lock_age_minutes "$lock_path")"
    if [ "$pid" != "unknown" ] && kill -0 "$pid" 2>/dev/null; then
        pid_state="pid-active"
    else
        pid_state="pid-missing"
    fi

    printf '%s runner=%s pid=%s started_at=%s age=%smin %s\n' \
        "$lock_name" "$runner" "$pid" "$started_at" "$age" "$pid_state"
}

print_aqsp_cron_audit() {
    local cron_text cron_line cron_file action
    cron_text="$(crontab -l 2>/dev/null || true)"

    echo "BT wrapper entries:"
    if [ -d /www/server/cron ]; then
        for cron_file in /www/server/cron/*; do
            [ -f "$cron_file" ] || continue
            case "$cron_file" in
                *.log|*.lock|*.pl) continue ;;
            esac
            if grep -q "bt_task.sh" "$cron_file" 2>/dev/null; then
                action="$(grep -Eo 'bt_task\.sh[[:space:]]+[a-z]+' "$cron_file" | awk '{print $2}' | head -n 1)"
                printf 'bt-wrapper action=%s script=%s\n' "${action:-unknown}" "$cron_file"
            elif grep -q "$PROJECT_ROOT" "$cron_file" 2>/dev/null; then
                printf 'project-cron-wrapper-needs-review script=%s\n' "$cron_file"
            fi
        done
    else
        echo "bt-cron-dir missing: /www/server/cron"
    fi

    echo "Direct AQSP crontab entries:"
    while IFS= read -r cron_line; do
        [ -n "$cron_line" ] || continue
        case "$cron_line" in
            \#*|LANG=*|LC_ALL=*) continue ;;
        esac
        if printf '%s\n' "$cron_line" | grep -qE "(/opt/aqsp|${PROJECT_ROOT})" \
            && ! printf '%s\n' "$cron_line" | grep -q "/www/server/cron/"; then
            printf 'direct-aqsp-cron-needs-review %s\n' "$cron_line"
        fi
    done <<EOF
$cron_text
EOF
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

print_section "CRON AQSP AUDIT"
print_aqsp_cron_audit

print_section "LOCKS"
printf 'config runner_timeout=%ss monitor_timeout=%ss stale_after=%smin\n' \
    "$RUNNER_TIMEOUT_SECONDS" "$MONITOR_TIMEOUT_SECONDS" "$LOCK_STALE_MINUTES"
print_lock_state "${PROJECT_ROOT}/.locks/server-runtime.lock"
print_lock_state "${PROJECT_ROOT}/.locks/server-monitor.lock"

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

print_section "DOCTOR"
if [ -f "${PROJECT_ROOT}/.venv/bin/python3" ] && [ -f "${PROJECT_ROOT}/src/aqsp/cli.py" ]; then
    ( cd "${PROJECT_ROOT}" && "${PROJECT_ROOT}/.venv/bin/python3" -m aqsp doctor ) || true
else
    echo "aqsp doctor unavailable"
fi

print_section "DEPLOY LOG"
tail -n 40 "${PROJECT_ROOT}/logs/deploy/sync-$(date +%Y-%m-%d).log" 2>/dev/null || true

print_section "BT TASK LOG"
for action in intraday midday daily coldstart monitor news status; do
    echo "--- ${action} ---"
    tail -n 20 "${PROJECT_ROOT}/logs/bt/bt-${action}-$(date +%Y-%m-%d).log" 2>/dev/null || true
done

print_section "INTRADAY LOG"
tail -n 40 "${PROJECT_ROOT}/logs/intraday/intraday-$(date +%Y-%m-%d).log" 2>/dev/null || true

print_section "DAILY LOG"
tail -n 40 "${PROJECT_ROOT}/logs/daily/pipeline-$(date +%Y-%m-%d).log" 2>/dev/null || true
