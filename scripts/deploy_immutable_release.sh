#!/usr/bin/env bash
# Build, activate, verify, and prune one immutable AQSP server release.
set -euo pipefail

REPO_ROOT="${AQSP_REPO_ROOT:-/opt/aqsp}"
RELEASES_ROOT="${AQSP_RELEASES_ROOT:-/opt/aqsp-releases}"
RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-/opt/aqsp}"
RUNTIME_DATA_ROOT="${AQSP_RUNTIME_DATA_ROOT:-${RUNTIME_ROOT}/data}"
CURRENT_LINK="${AQSP_RELEASE_CURRENT_LINK:-${RELEASES_ROOT}/aqsp-scheduler-current}"
ROLLBACK_LINK="${AQSP_RELEASE_ROLLBACK_LINK:-${RELEASES_ROOT}/aqsp-scheduler-rollback}"
SHARED_VENV_DIR="${AQSP_SHARED_VENV_DIR:-/opt/aqsp-vibe-venv}"
PYTHON_BIN="${AQSP_RUNTIME_PYTHON:-${SHARED_VENV_DIR}/bin/python3}"
NPM_BIN="${AQSP_NPM_BIN:-/usr/bin/npm}"
REMOTE="${AQSP_GIT_REMOTE:-origin}"
BRANCH="${AQSP_GIT_BRANCH:-main}"
REF="${AQSP_RELEASE_REF:-$BRANCH}"
CHECK_URL="${AQSP_DEPLOY_CHECK_URL:-https://lh.ifidy.cn}"
API_SERVICE="${AQSP_API_SERVICE:-aqsp-vibe-research-api.service}"
PREVIEW_SERVICE="${AQSP_PREVIEW_SERVICE:-aqsp-vibe-research-preview.service}"
TARGET_SERVICE="${AQSP_VIBE_SYSTEMD_TARGET:-aqsp-vibe-research.target}"
API_PORT="${AQSP_API_PORT:-8900}"
FRONTEND_PORT="${AQSP_FRONTEND_PORT:-5899}"
SERVICE_USER="${AQSP_VIBE_USER:-aqsp-vibe}"
SERVICE_GROUP="${AQSP_VIBE_GROUP:-${SERVICE_USER}}"
LOCK_DIR="${AQSP_RUNTIME_LOCK_DIR:-${RUNTIME_DATA_ROOT}/.locks}"
LOCK_FILE="${LOCK_DIR}/immutable-release-deploy.lock"
HEADLESS_LOCK_FILE="${AQSP_HEADLESS_LOCK:-/tmp/aqsp-headless-dashboard.lock}"
EXPECTED_VARIANT_END="${AQSP_DEPLOY_EXPECTED_VARIANT_END:-}"
SKIP_FRONTEND_BUILD="false"
SKIP_RESTART="false"
SKIP_PUBLIC_CHECK="false"
SKIP_SCHEDULER_CHECK="false"

usage() {
    cat <<'USAGE'
usage: scripts/deploy_immutable_release.sh [--ref REF] [--branch BRANCH]
                                           [--skip-frontend-build]
                                           [--skip-restart]
                                           [--skip-public-check]
                                           [--skip-scheduler-check]
                                           [--check-url URL]

Creates /opt/aqsp-releases/<commit> from Git, builds frontend dependencies before
activation, writes the manifest after the final release path is known, switches
current/rollback atomically, restarts API/preview, checks content, then prunes
only unprotected release residuals.
USAGE
}

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fail() {
    printf 'immutable release deploy failed: %s\n' "$*" >&2
    exit 1
}

quote() {
    printf "'%s'" "${1//\'/\'\\\'\'}"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --ref) REF="${2:?--ref requires value}"; shift 2 ;;
        --branch) BRANCH="${2:?--branch requires value}"; shift 2 ;;
        --skip-frontend-build) SKIP_FRONTEND_BUILD="true"; shift ;;
        --skip-restart) SKIP_RESTART="true"; shift ;;
        --skip-public-check) SKIP_PUBLIC_CHECK="true"; shift ;;
        --skip-scheduler-check) SKIP_SCHEDULER_CHECK="true"; shift ;;
        --check-url) CHECK_URL="${2:?--check-url requires value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown argument: $1" ;;
    esac
done

case "$REPO_ROOT" in /*) ;; *) fail "AQSP_REPO_ROOT must be absolute" ;; esac
case "$RELEASES_ROOT" in /*) ;; *) fail "AQSP_RELEASES_ROOT must be absolute" ;; esac
case "$RUNTIME_DATA_ROOT" in /*) ;; *) fail "AQSP_RUNTIME_DATA_ROOT must be absolute" ;; esac
[ -d "$REPO_ROOT/.git" ] || fail "Git repo missing: $REPO_ROOT"
[ -x "$PYTHON_BIN" ] || fail "runtime python missing or not executable: $PYTHON_BIN"
[ -x "$NPM_BIN" ] || fail "npm missing or not executable: $NPM_BIN"
mkdir -p "$RELEASES_ROOT" "$LOCK_DIR"

if ! mkdir "$LOCK_FILE" 2>/dev/null; then
    fail "another immutable release deployment is running: $LOCK_FILE"
fi
STAGE_DIR=""
cleanup() {
    [ -n "$STAGE_DIR" ] && rm -rf -- "$STAGE_DIR"
    rmdir "$LOCK_FILE" 2>/dev/null || true
}
trap cleanup EXIT

resolve_commit() {
    cd "$REPO_ROOT"
    if [ "$REF" = "$BRANCH" ]; then
        git fetch "$REMOTE" "refs/heads/${BRANCH}:refs/remotes/${REMOTE}/${BRANCH}"
        git rev-parse "refs/remotes/${REMOTE}/${BRANCH}^{commit}"
    else
        git fetch "$REMOTE" "$BRANCH"
        git rev-parse "${REF}^{commit}"
    fi
}

build_frontend() {
    local root="$1"
    [ -d "$root/frontend" ] || fail "frontend directory missing in release: $root/frontend"
    [ -f "$root/frontend/package-lock.json" ] || fail "frontend package-lock.json missing"
    log "install frontend dependencies"
    (cd "$root/frontend" && "$NPM_BIN" ci)
    log "build frontend dist"
    (cd "$root/frontend" && "$NPM_BIN" run build)
    log "check frontend audit"
    "$PYTHON_BIN" "$root/scripts/check_frontend_audit.py" --frontend-dir "$root/frontend"
}

stamp_manifest() {
    local root="$1" commit="$2" remote_url
    remote_url="$(cd "$REPO_ROOT" && git config --get "remote.${REMOTE}.url" || printf unknown)"
    PYTHONPATH="$root/src" "$PYTHON_BIN" "$root/scripts/write_release_manifest.py" \
        --root "$root" \
        --commit "$commit" \
        --branch "$BRANCH" \
        --remote "$REMOTE" \
        --remote-url "$remote_url"
}

normalize_release_modes() {
    local root="$1"
    find "$root" -type d -exec chmod 755 {} +
    find "$root" -type f -exec chmod a+r {} +
    test -x "$root/scripts/bt_task.sh"
    test -x "$root/scripts/health_vibe_research.sh"
}

prepare_frontend_runtime_cache() {
    local root="$1"
    [ -d "$root/frontend/node_modules" ] || return 0
    id "$SERVICE_USER" >/dev/null 2>&1 || fail "service user missing: $SERVICE_USER"
    getent group "$SERVICE_GROUP" >/dev/null 2>&1 || fail "service group missing: $SERVICE_GROUP"
    install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" \
        "$root/frontend/node_modules/.vite-temp" \
        "$root/frontend/node_modules/.vite"
    chown -R "$SERVICE_USER:$SERVICE_GROUP" \
        "$root/frontend/node_modules/.vite-temp" \
        "$root/frontend/node_modules/.vite"
}

check_release() {
    local root="$1"
    PYTHONPATH="$root/src" "$PYTHON_BIN" "$root/scripts/check_release_consistency.py" \
        --project-root "$root" \
        --manifest "$root/.aqsp-release.json" \
        --branch "$BRANCH" \
        --immutable-release \
        --active-file scripts/release_task_entrypoint.sh \
        --active-file scripts/bt_task.sh \
        --executable-file scripts/bt_task.sh \
        --executable-file scripts/health_vibe_research.sh
}

check_current_release() {
    local root="$1"
    PYTHONPATH="$root/src" "$PYTHON_BIN" "$root/scripts/check_release_consistency.py" \
        --project-root "$root" \
        --canonical-link "$CURRENT_LINK" \
        --manifest "$root/.aqsp-release.json" \
        --branch "$BRANCH" \
        --immutable-release \
        --active-file scripts/release_task_entrypoint.sh \
        --active-file scripts/bt_task.sh \
        --executable-file scripts/bt_task.sh \
        --executable-file scripts/health_vibe_research.sh
}

stop_stale_frontend_port_owner() {
    local pid cmd cwd
    systemctl stop "$PREVIEW_SERVICE" >/dev/null 2>&1 || true
    if ! command -v ss >/dev/null 2>&1; then
        return 0
    fi
    for pid in $(ss -ltnp "( sport = :$FRONTEND_PORT )" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u); do
        cmd="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
        cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
        case "$cmd $cwd" in
            *vite\ preview*"$FRONTEND_PORT"*|*/opt/aqsp/data/vibe-research/frontend*)
                log "stop stale frontend port owner pid=$pid cwd=$cwd"
                kill "$pid" 2>/dev/null || true
                ;;
            *)
                fail "unexpected frontend port owner pid=$pid cwd=$cwd cmd=$cmd"
                ;;
        esac
    done
    sleep 2
}

switch_links() {
    local release="$1" old_current=""
    if [ -L "$CURRENT_LINK" ]; then
        old_current="$(readlink -f "$CURRENT_LINK")"
    fi
    if [ -n "$old_current" ] && [ "$old_current" != "$release" ]; then
        ln -sfn "$old_current" "${ROLLBACK_LINK}.tmp"
        mv -Tf "${ROLLBACK_LINK}.tmp" "$ROLLBACK_LINK"
    fi
    ln -sfn "$release" "${CURRENT_LINK}.tmp"
    mv -Tf "${CURRENT_LINK}.tmp" "$CURRENT_LINK"
}

restart_services() {
    local release="$1" api_pid preview_pid api_cwd preview_cwd
    systemctl restart "$API_SERVICE"
    stop_stale_frontend_port_owner
    systemctl restart "$PREVIEW_SERVICE"
    # The target is the persistent service contract. Restarting its members
    # alone can leave the group inactive while both ports still respond.
    systemctl start "$TARGET_SERVICE"
    sleep 4
    systemctl is-active --quiet "$API_SERVICE" || fail "$API_SERVICE is not active"
    systemctl is-active --quiet "$PREVIEW_SERVICE" || fail "$PREVIEW_SERVICE is not active"
    systemctl is-active --quiet "$TARGET_SERVICE" || fail "$TARGET_SERVICE is not active"
    api_pid="$(systemctl show -p MainPID --value "$API_SERVICE")"
    preview_pid="$(systemctl show -p MainPID --value "$PREVIEW_SERVICE")"
    api_cwd="$(readlink -f "/proc/$api_pid/cwd")"
    preview_cwd="$(readlink -f "/proc/$preview_pid/cwd")"
    [ "$api_cwd" = "$release/backend" ] || fail "API cwd drift: $api_cwd"
    [ "$preview_cwd" = "$release/frontend" ] || fail "preview cwd drift: $preview_cwd"
    curl -fsS --max-time 10 "http://127.0.0.1:${API_PORT}/api/health" >/dev/null
}

check_public_routes() {
    [ "$SKIP_PUBLIC_CHECK" = "true" ] && return 0
    [ -n "$CHECK_URL" ] || return 0
    case "$CHECK_URL" in https://*) ;; *) fail "public check URL must be https://: $CHECK_URL" ;; esac
    for path in / /daily-review /variants /api/health /api/aqsp/snapshot; do
        curl -fsS --max-time 12 "${CHECK_URL%/}${path}" >/dev/null
    done
    if [ -z "$EXPECTED_VARIANT_END" ]; then
        EXPECTED_VARIANT_END="$(PYTHONPATH="$RELEASE_DIR/src" "$PYTHON_BIN" - <<'PY'
from aqsp.core.time import latest_completed_trading_day

print(latest_completed_trading_day().isoformat())
PY
)"
    fi
    "$PYTHON_BIN" - "${CHECK_URL%/}/api/aqsp/snapshot" "$RELEASE_DIR/src" "$EXPECTED_VARIANT_END" <<'PY'
import json
import math
import sys
from datetime import date

sys.path.insert(0, sys.argv[2])
from aqsp.core.http import urlopen_no_macos_proxy

url = sys.argv[1]
expected_end = sys.argv[3]
payload = json.loads(urlopen_no_macos_proxy(url, timeout=12).read().decode())
data = payload.get("data", payload)
selected = data.get("selected_date")
available = data.get("available_dates")
if not isinstance(selected, str) or not selected:
    raise SystemExit("snapshot selected_date missing")
date.fromisoformat(selected)
if not isinstance(available, list) or selected not in available:
    raise SystemExit("snapshot available_dates missing selected_date")
source = data.get("source") if isinstance(data.get("source"), dict) else {}
latest_trade_date = str(source.get("latest_trade_date") or "")
universe = data.get("universe") if isinstance(data.get("universe"), dict) else {}
gate = data.get("recommendation_gate")
partial_raw_refresh = (
    isinstance(gate, dict)
    and gate.get("recommendation_allowed") is False
    and gate.get("status") == "blocked_incomplete_raw_data"
    and universe.get("source") == "sqlite_raw_refresh"
    and isinstance(universe.get("total"), int)
    and isinstance(universe.get("resolved"), int)
    and 0 <= universe["resolved"] < universe["total"]
    and str(universe.get("batch_id") or "") == expected_end
)
if expected_end and selected != expected_end and not partial_raw_refresh:
    raise SystemExit(f"snapshot selected_date {selected} != expected {expected_end}")
if expected_end and latest_trade_date not in {"", "未记录", expected_end}:
    raise SystemExit(
        f"snapshot latest_trade_date {latest_trade_date} != expected {expected_end}"
    )
if (
    expected_end
    and latest_trade_date in {"", "未记录"}
    and not partial_raw_refresh
):
    raise SystemExit("snapshot latest_trade_date missing outside blocked raw refresh")
variant_suite = data.get("variant_suite")
if not isinstance(variant_suite, dict):
    raise SystemExit("snapshot variant_suite missing")
if partial_raw_refresh:
    print(
        "snapshot_contract blocked_incomplete_raw_data",
        f"selected_date={selected}",
        f"raw_coverage={universe['resolved']}/{universe['total']}",
    )
    raise SystemExit(0)
suite_end = str(variant_suite.get("end_date") or "")
if expected_end and suite_end != expected_end:
    raise SystemExit(f"snapshot variant_suite end_date {suite_end} != expected {expected_end}")
variants = data.get("variants")
if not isinstance(variants, list) or not variants or not isinstance(variants[0], dict):
    raise SystemExit("snapshot variants missing")
declared_variant_count = variant_suite.get("variant_count")
if not isinstance(declared_variant_count, int) or declared_variant_count < 100:
    raise SystemExit("snapshot variant_suite variant_count < 100")
if len(variants) < 100:
    raise SystemExit("snapshot variants < 100")
first = variants[0]
previous_holdings_date = str(first.get("previous_holdings_date") or "")
if expected_end and first.get("holdings_date") != expected_end:
    raise SystemExit("snapshot first variant holdings_date mismatch")
if previous_holdings_date and previous_holdings_date not in available:
    raise SystemExit("snapshot available_dates missing previous_holdings_date")
if not first.get("adjustments"):
    raise SystemExit("snapshot first variant adjustments empty")

def _is_finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False

technical_evidence = first.get("technical_evidence")
if not isinstance(technical_evidence, list) or not any(
    isinstance(evidence, dict)
    and all(
        not isinstance(evidence.get(key), bool)
        and _is_finite(evidence.get(key))
        for key in ("macd_hist", "kdj_j", "volume_ratio", "atr_pct")
    )
    for evidence in technical_evidence
):
    raise SystemExit("snapshot first variant technical_evidence incomplete")
for key in ("candidates", "debates", "summaries", "messages"):
    value = data.get(key)
    if value is not None and not isinstance(value, list):
        raise SystemExit(f"snapshot {key} must be a list")
print(
    "snapshot_contract",
    f"selected_date={selected}",
    f"dates={len(available)}",
    f"candidates={len(data.get('candidates') or [])}",
    f"debates={len(data.get('debates') or [])}",
    f"messages={len(data.get('messages') or [])}",
)
PY
    AQSP_HEADLESS_LOCK="$HEADLESS_LOCK_FILE" "$PYTHON_BIN" "$RELEASE_DIR/scripts/headless_dashboard_check.py" \
        --url "$CHECK_URL" \
        --mode browser \
        --require-browser \
        --headless-lock "$HEADLESS_LOCK_FILE"
}

check_variant_results() {
    local variant_path="${AQSP_VARIANT_RESULTS:-${RUNTIME_DATA_ROOT}/runtime/variant_results.json}"
    [ -f "$variant_path" ] || fail "variant results missing: $variant_path"
    local command=(
        "$PYTHON_BIN" "$RELEASE_DIR/scripts/check_variant_results.py"
        "$variant_path"
        --min-variants 100
        --min-symbols 121
    )
    if [ -n "$EXPECTED_VARIANT_END" ]; then
        command+=(--expected-end "$EXPECTED_VARIANT_END")
    fi
    PYTHONPATH="$RELEASE_DIR/src:$RELEASE_DIR/scripts" "${command[@]}"
}

cleanup_release_root_residuals() {
    find "$RELEASES_ROOT" -mindepth 1 -maxdepth 1 -type f -delete
    find "$RELEASES_ROOT" -mindepth 1 -maxdepth 1 -type d -name '.stage-*' -exec rm -rf {} +
    if ps -ef | grep -F "$RUNTIME_ROOT/data/vibe-research/frontend" | grep -v grep; then
        fail "legacy runtime frontend still has a live process"
    fi
    rm -rf -- "$RUNTIME_ROOT/data/vibe-research/frontend"
}

prune_releases() {
    local release="$1"
    PYTHONPATH="$release/src" "$PYTHON_BIN" "$release/scripts/check_runtime_storage.py" \
        --apply --json --env-file "$RUNTIME_ROOT/.env"
}

run_scheduler_check() {
    local release="$1"
    [ "$SKIP_SCHEDULER_CHECK" = "true" ] && return 0
    PYTHONPATH="$release/src" \
    AQSP_PROJECT_ROOT="$release" \
    AQSP_RUNTIME_ROOT="$RUNTIME_ROOT" \
    AQSP_RUNTIME_DATA_ROOT="$RUNTIME_DATA_ROOT" \
    AQSP_RUNTIME_PYTHON="$PYTHON_BIN" \
    AQSP_SCHEDULER_STRICT_SCHEDULE=true \
        "$PYTHON_BIN" "$release/scripts/check_scheduler.py"
}

COMMIT="$(resolve_commit)"
case "$COMMIT" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;; *) fail "invalid commit: $COMMIT" ;; esac
RELEASE_DIR="${RELEASES_ROOT}/${COMMIT}"
log "deploy commit=$COMMIT branch=$BRANCH release=$RELEASE_DIR"

if [ "$SKIP_FRONTEND_BUILD" = "true" ] && { [ ! -d "$RELEASE_DIR/frontend/node_modules" ] || [ ! -d "$RELEASE_DIR/frontend/dist" ]; }; then
    fail "--skip-frontend-build requires an existing release with complete frontend artifacts"
fi

if [ ! -d "$RELEASE_DIR" ]; then
    STAGE_DIR="$(mktemp -d "${RELEASES_ROOT}/.stage-${COMMIT}.XXXXXX")"
    (cd "$REPO_ROOT" && git archive --format=tar "$COMMIT") | tar -x -C "$STAGE_DIR"
    if [ "$SKIP_FRONTEND_BUILD" != "true" ]; then
        build_frontend "$STAGE_DIR"
    fi
    normalize_release_modes "$STAGE_DIR"
    mv "$STAGE_DIR" "$RELEASE_DIR"
    STAGE_DIR=""
elif [ "$SKIP_FRONTEND_BUILD" != "true" ] && { [ ! -d "$RELEASE_DIR/frontend/node_modules" ] || [ ! -d "$RELEASE_DIR/frontend/dist" ]; }; then
    log "repair incomplete frontend artifacts in existing release"
    build_frontend "$RELEASE_DIR"
fi

stamp_manifest "$RELEASE_DIR" "$COMMIT"
normalize_release_modes "$RELEASE_DIR"
prepare_frontend_runtime_cache "$RELEASE_DIR"
check_release "$RELEASE_DIR"
# The BaoTa wrappers are external state. Reject schedule drift before the
# symlink/service switch so a failed acceptance never leaves a half deployment.
run_scheduler_check "$RELEASE_DIR"
switch_links "$RELEASE_DIR"
check_current_release "$RELEASE_DIR"

VERIFY_LEVEL="full"
if [ "$SKIP_RESTART" = "true" ] || [ "$SKIP_PUBLIC_CHECK" = "true" ] || [ "$SKIP_SCHEDULER_CHECK" = "true" ]; then
    VERIFY_LEVEL="partial"
fi

if [ "$SKIP_RESTART" != "true" ]; then
    restart_services "$RELEASE_DIR"
fi
check_public_routes
if ! check_variant_results; then
    VERIFY_LEVEL="partial"
    log "[WARN] 变体产物未通过校验；release 已切换，但本次部署未验收"
fi
cleanup_release_root_residuals
prune_releases "$RELEASE_DIR"
if [ "$VERIFY_LEVEL" = "full" ]; then
    log "immutable release deployment verified: $RELEASE_DIR"
else
    log "immutable release prepared with skipped checks: $RELEASE_DIR"
fi
