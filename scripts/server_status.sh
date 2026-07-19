#!/usr/bin/env bash
# 服务器状态总览：
# 1. Git 状态
# 2. Cron 任务
# 3. 关键产物
# 4. 最新日志

set -euo pipefail

# Code lives in the active immutable release; runtime data remains under
# /opt/aqsp. Resolve the release only when the caller did not pin a root.
if [ -n "${AQSP_PROJECT_ROOT:-}" ]; then
    PROJECT_ROOT="$AQSP_PROJECT_ROOT"
elif [ -L /opt/aqsp-releases/aqsp-scheduler-current ]; then
    PROJECT_ROOT="$(readlink -f /opt/aqsp-releases/aqsp-scheduler-current)"
else
    PROJECT_ROOT="/opt/aqsp"
fi
RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-/opt/aqsp}"
LOCK_STALE_MINUTES="${AQSP_LOCK_STALE_MINUTES:-360}"
RUNNER_TIMEOUT_SECONDS="${AQSP_RUNNER_TIMEOUT_SECONDS:-0}"
MONITOR_TIMEOUT_SECONDS="${AQSP_MONITOR_TIMEOUT_SECONDS:-0}"

if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
fi

RUNTIME_PYTHON="${AQSP_RUNTIME_PYTHON:-}"
if [ -z "$RUNTIME_PYTHON" ]; then
    RUNTIME_PYTHON_HELPER="${PROJECT_ROOT}/scripts/runtime_python.sh"
    if [ ! -f "$RUNTIME_PYTHON_HELPER" ]; then
        RUNTIME_PYTHON_HELPER="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}/runtime_python.sh"
    fi
    if [ -f "$RUNTIME_PYTHON_HELPER" ]; then
        # Status checks must inspect the same immutable release as scheduled tasks.
        # shellcheck disable=SC1090
        source "$RUNTIME_PYTHON_HELPER"
        RUNTIME_PYTHON="$(aqsp_runtime_python "$PROJECT_ROOT")"
    fi
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
            value="${value%\"}"
            value="${value#\"}"
            value="${value%\'}"
            value="${value#\'}"
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
    local cron_text cron_line cron_file cron_id cron_schedule action time_gate day_gate env_hint found_wrapper found_direct
    cron_text="$(crontab -l 2>/dev/null || true)"
    found_wrapper=0
    found_direct=0

    echo "BT wrapper entries:"
    if [ -d /www/server/cron ]; then
        for cron_file in /www/server/cron/*; do
            [ -f "$cron_file" ] || continue
            case "$cron_file" in
                *.log|*.lock|*.pl) continue ;;
            esac
            if grep -q "bt_task.sh" "$cron_file" 2>/dev/null; then
                cron_id="$(basename "$cron_file")"
                cron_schedule="$(printf '%s\n' "$cron_text" | awk -v id="$cron_id" '$0 ~ id {print $1" "$2" "$3" "$4" "$5; exit}')"
                [ -n "$cron_schedule" ] || cron_schedule="not-installed"
                action="$(grep -Eo 'bt_task\.sh[[:space:]]+[a-z]+' "$cron_file" | awk '{print $2}' | head -n 1 || true)"
                time_gate="$(grep -Eo 'special_time=[0-9:,]+' "$cron_file" | head -n 1 | cut -d= -f2 || true)"
                [ -n "$time_gate" ] || time_gate="-"
                day_gate="$(grep -Eo 'time_list=[0-9,]+' "$cron_file" | head -n 1 | cut -d= -f2 || true)"
                case "$day_gate" in
                    1,2,3,4,5) day_gate="Mon-Fri" ;;
                    6,7) day_gate="Sat-Sun" ;;
                    "") day_gate="-" ;;
                esac
                if [ "$day_gate" = "-" ]; then
                    case "${action:-}" in
                        daily|coldstart)
                            day_gate="script:Mon-Fri"
                            ;;
                        midday)
                            day_gate="script:Mon-Fri"
                            time_gate="script:11:35-12:30"
                            ;;
                        intraday)
                            day_gate="script:Mon-Fri"
                            time_gate="script:09:35-11:30/13:05-14:57"
                            ;;
                    esac
                fi
                env_hint="$(grep -Eo 'AQSP_[A-Z0-9_]+=[^[:space:]]+' "$cron_file" | tr '\n' ',' | sed 's/,$//' || true)"
                [ -n "$env_hint" ] || env_hint="-"
                printf 'bt-wrapper action=%s cron="%s" gate="%s" days="%s" env="%s" script=%s\n' \
                    "${action:-unknown}" "$cron_schedule" "$time_gate" "$day_gate" "$env_hint" "$cron_file"
                found_wrapper=1
            elif grep -q "$PROJECT_ROOT" "$cron_file" 2>/dev/null; then
                printf 'project-cron-wrapper-needs-review script=%s\n' "$cron_file"
                found_wrapper=1
            fi
        done
        if [ "$found_wrapper" -eq 0 ]; then
            echo "none"
        fi
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
            found_direct=1
        fi
    done <<EOF
$cron_text
EOF
    if [ "$found_direct" -eq 0 ]; then
        echo "none"
    fi
}

file_line() {
    local path="$1"
    if [ -e "$path" ]; then
        ls -lh "$path"
    else
        printf 'missing %s\n' "$path"
    fi
}

critical_status=0

record_critical_status() {
    local label="$1"
    local status="$2"
    if [ "$status" -eq 0 ]; then
        return
    fi

    printf 'critical check failed: %s (exit=%s)\n' "$label" "$status" >&2
    if [ "$critical_status" -eq 0 ]; then
        critical_status="$status"
    fi
}

run_critical_check() {
    local label="$1"
    shift
    local status
    if "$@"; then
        return 0
    else
        status="$?"
    fi

    record_critical_status "$label" "$status"
}

run_project_check() {
    ( cd "$PROJECT_ROOT" && PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}" "$@" )
}

print_section "GIT"
cd "$PROJECT_ROOT"
if [ -d "${PROJECT_ROOT}/.git" ]; then
    git log --oneline -3
    tracked_status="$(git status --short --untracked-files=no)"
    if [ -n "$tracked_status" ]; then
        printf '%s\n' "$tracked_status"
    else
        echo "tracked working tree clean"
    fi
    untracked_count="$(git status --short --untracked-files=normal | awk '$1 == "??" {count++} END {print count + 0}')"
    echo "untracked runtime files: ${untracked_count} (omitted)"
else
    echo "immutable release: ${PROJECT_ROOT} (git metadata not required)"
fi

print_section "CRON"
crontab -l 2>/dev/null || true

print_section "CRON AQSP AUDIT"
print_aqsp_cron_audit

print_section "LOCKS"
printf 'config runner_timeout=%ss monitor_timeout=%ss stale_after=%smin\n' \
    "$RUNNER_TIMEOUT_SECONDS" "$MONITOR_TIMEOUT_SECONDS" "$LOCK_STALE_MINUTES"
print_lock_state "${RUNTIME_ROOT}/.locks/server-runtime.lock"
print_lock_state "${RUNTIME_ROOT}/.locks/server-monitor.lock"

print_section "ARTIFACTS"
file_line "${RUNTIME_ROOT}/reports/latest.md"
file_line "${RUNTIME_ROOT}/reports/latest.csv"
file_line "${RUNTIME_ROOT}/reports/briefing.md"
file_line "${RUNTIME_ROOT}/reports/intraday_latest.md"
file_line "${RUNTIME_ROOT}/reports/intraday_latest.csv"
file_line "${RUNTIME_ROOT}/dist/dashboard/index.html"
file_line "${RUNTIME_ROOT}/dist/dashboard/aqsp.db"

print_section "RUNTIME"
if [ -x "$RUNTIME_PYTHON" ] && [ -f "${PROJECT_ROOT}/scripts/diagnose_runtime.py" ]; then
    "$RUNTIME_PYTHON" "${PROJECT_ROOT}/scripts/diagnose_runtime.py" || true
else
    echo "diagnose_runtime unavailable"
fi

print_section "DOCTOR"
if [ -x "$RUNTIME_PYTHON" ] && [ -f "${PROJECT_ROOT}/src/aqsp/cli.py" ]; then
    run_critical_check "aqsp doctor" run_project_check "$RUNTIME_PYTHON" -m aqsp doctor
else
    echo "aqsp doctor unavailable"
    record_critical_status "aqsp doctor unavailable" 127
fi

print_section "BEFORE LIVE"
if [ -x "$RUNTIME_PYTHON" ] && [ -f "${PROJECT_ROOT}/scripts/check_before_live.py" ]; then
    run_critical_check "check_before_live" run_project_check "$RUNTIME_PYTHON" scripts/check_before_live.py
else
    echo "check_before_live unavailable"
    record_critical_status "check_before_live unavailable" 127
fi

print_section "REMOTE PROBE"
if [ -f "${PROJECT_ROOT}/scripts/remote_runtime_probe.py" ]; then
    run_critical_check "remote_runtime_probe" run_project_check "$RUNTIME_PYTHON" scripts/remote_runtime_probe.py
else
    echo "remote_runtime_probe unavailable"
    record_critical_status "remote_runtime_probe unavailable" 127
fi

print_section "DEPLOY LOG"
tail -n 40 "${RUNTIME_ROOT}/logs/deploy/sync-$(date +%Y-%m-%d).log" 2>/dev/null || true

print_section "BT TASK LOG"
for action in intraday midday daily coldstart monitor news; do
    echo "--- ${action} ---"
    tail -n 20 "${RUNTIME_ROOT}/logs/bt/bt-${action}-$(date +%Y-%m-%d).log" 2>/dev/null || true
done

print_section "INTRADAY LOG"
tail -n 40 "${RUNTIME_ROOT}/logs/intraday/intraday-$(date +%Y-%m-%d).log" 2>/dev/null || true

print_section "DAILY LOG"
tail -n 40 "${RUNTIME_ROOT}/logs/daily/pipeline-$(date +%Y-%m-%d).log" 2>/dev/null || true

if [ "$critical_status" -ne 0 ]; then
    printf 'critical checks failed; server status exit=%s\n' "$critical_status" >&2
    exit "$critical_status"
fi
