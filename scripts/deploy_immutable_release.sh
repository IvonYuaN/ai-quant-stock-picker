#!/usr/bin/env bash
# Build, activate, verify, and prune one immutable AQSP server release.
#
# Adapted from PR #46 (codex/monitor-walkforward) for the AQSP repo.
# vs upstream this version:
#   * release-local data/ inheritance (upstream assumed a shared /opt/aqsp/data
#     and even rm -rf'd the legacy runtime frontend — our prod/runner keep
#     data/ + reports/ INSIDE each release and inherit them on cut-over)
#   * idle-window guard before cut-over (refuse while a gate/walkforward run is
#     in flight, or outside an optional allowed window)
#   * target-aware: prod (systemd) or runner (PYTHONPATH, no systemd)
#   * CHECK_URL defaults to empty (no probing of a foreign domain)
#
# All paths are env-overridable so the same script drives prod and runner.
set -euo pipefail

# ---------------------------------------------------------------------------
# target + path resolution (every path env-overridable)
# ---------------------------------------------------------------------------
TARGET="${AQSP_DEPLOY_TARGET:-prod}"
case "$TARGET" in
  prod)
    REPO_ROOT="${AQSP_REPO_ROOT:-/opt/aqsp}"
    RELEASES_ROOT="${AQSP_RELEASES_ROOT:-/opt/aqsp-releases}"
    RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-/opt/aqsp}"
    ;;
  runner)
    REPO_ROOT="${AQSP_REPO_ROOT:-/opt/aqsp-runner}"
    RELEASES_ROOT="${AQSP_RELEASES_ROOT:-/opt/aqsp-runner/releases}"
    RUNTIME_ROOT="${AQSP_RUNTIME_ROOT:-/opt/aqsp-runner}"
    ;;
  *) fail "unknown AQSP_DEPLOY_TARGET: $TARGET (expected prod|runner)" ;;
esac

CURRENT_LINK="${AQSP_RELEASE_CURRENT_LINK:-${RELEASES_ROOT}/aqsp-scheduler-current}"
ROLLBACK_LINK="${AQSP_RELEASE_ROLLBACK_LINK:-${RELEASES_ROOT}/aqsp-scheduler-rollback}"
SHARED_VENV_DIR="${AQSP_SHARED_VENV_DIR:-/opt/aqsp-vibe-venv}"
PYTHON_BIN="${AQSP_RUNTIME_PYTHON:-${SHARED_VENV_DIR}/bin/python3}"
NPM_BIN="${AQSP_NPM_BIN:-/usr/bin/npm}"
REMOTE="${AQSP_GIT_REMOTE:-origin}"
BRANCH="${AQSP_GIT_BRANCH:-main}"
REF="${AQSP_RELEASE_REF:-$BRANCH}"
CHECK_URL="${AQSP_DEPLOY_CHECK_URL:-}"
API_SERVICE="${AQSP_API_SERVICE:-aqsp-vibe-research-api.service}"
PREVIEW_SERVICE="${AQSP_PREVIEW_SERVICE:-aqsp-vibe-research-preview.service}"
TARGET_SERVICE="${AQSP_VIBE_SYSTEMD_TARGET:-aqsp-vibe-research.target}"
API_PORT="${AQSP_API_PORT:-8900}"
FRONTEND_PORT="${AQSP_FRONTEND_PORT:-5899}"
LOCAL_HEALTH_TIMEOUT_SECONDS="${AQSP_DEPLOY_LOCAL_HEALTH_TIMEOUT_SECONDS:-20}"
SERVICE_USER="${AQSP_VIBE_USER:-aqsp-vibe}"
SERVICE_GROUP="${AQSP_VIBE_GROUP:-${SERVICE_USER}}"
# lock dir lives OUTSIDE releases so it is shared across releases (prevents
# two concurrent deploys even though each release is immutable)
LOCK_DIR="${AQSP_RUNTIME_LOCK_DIR:-${RELEASES_ROOT}/.locks}"
LOCK_FILE="${LOCK_DIR}/immutable-release-deploy.lock"
HEADLESS_LOCK_FILE="${AQSP_HEADLESS_LOCK:-/tmp/aqsp-headless-dashboard.lock}"
EXPECTED_VARIANT_END="${AQSP_DEPLOY_EXPECTED_VARIANT_END:-}"
PREVIOUS_RELEASE=""
SKIP_FRONTEND_BUILD="false"
SKIP_RESTART="false"
SKIP_PUBLIC_CHECK="false"
SKIP_SCHEDULER_CHECK="false"
SKIP_VARIANT_CHECK="false"
INHERIT_DATA="true"
DRY_RUN="false"
FORCE="false"

usage() {
    cat <<'USAGE'
usage: scripts/deploy_immutable_release.sh [--target prod|runner] [--ref REF]
                                           [--branch BRANCH]
                                           [--skip-frontend-build]
                                           [--skip-restart]
                                           [--skip-public-check]
                                           [--skip-scheduler-check]
                                           [--skip-variant-check]
                                           [--no-inherit-data]
                                           [--dry-run]
                                           [--force]
                                           [--check-url URL]

Creates <releases_root>/<commit> from Git, inherits data/ + reports/ from the
current release, builds frontend deps before activation, writes the manifest,
switches current/rollback atomically, restarts API/preview (prod), checks
content, then prunes only unprotected release residuals.

Idle-window guard (--force to override):
  * refuses while a gate/walkforward process is running locally
  * refuses outside AQSP_DEPLOY_ALLOWED_WINDOW (HH:MM-HH:MM, local tz)
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
        --target) TARGET="${2:?--target requires value}"; shift 2 ;;
        --ref) REF="${2:?--ref requires value}"; shift 2 ;;
        --branch) BRANCH="${2:?--branch requires value}"; shift 2 ;;
        --skip-frontend-build) SKIP_FRONTEND_BUILD="true"; shift ;;
        --skip-restart) SKIP_RESTART="true"; shift ;;
        --skip-public-check) SKIP_PUBLIC_CHECK="true"; shift ;;
        --skip-scheduler-check) SKIP_SCHEDULER_CHECK="true"; shift ;;
        --skip-variant-check) SKIP_VARIANT_CHECK="true"; shift ;;
        --no-inherit-data) INHERIT_DATA="false"; shift ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --force) FORCE="true"; shift ;;
        --check-url) CHECK_URL="${2:?--check-url requires value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown argument: $1" ;;
    esac
done

case "$TARGET" in prod|runner) ;; *) fail "unknown target: $TARGET" ;; esac
case "$REPO_ROOT" in /*) ;; *) fail "AQSP_REPO_ROOT must be absolute" ;; esac
case "$RELEASES_ROOT" in /*) ;; *) fail "AQSP_RELEASES_ROOT must be absolute" ;; esac

# ---------------------------------------------------------------------------
# idle-window guard — must pass before we touch anything
# ---------------------------------------------------------------------------
assert_idle_window() {
    [ "$FORCE" = "true" ] && return 0
    # never cut over while a heavy gate/walkforward run is in flight here
    if pgrep -f "[a]qsp walkforward" >/dev/null 2>&1 || pgrep -f "[b]t_task[.]sh" >/dev/null 2>&1; then
        fail "idle-window guard: a gate/walkforward process is currently running; deploy would disrupt it. Use --force to override."
    fi
    local win="${AQSP_DEPLOY_ALLOWED_WINDOW:-}"
    [ -z "$win" ] && return 0
    local now_hm start end
    now_hm="$(date '+%H:%M')"
    start="${win%-*}"
    end="${win#*-}"
    if [ "$now_hm" \< "$start" ] || [ "$now_hm" \> "$end" ]; then
        fail "idle-window guard: current time $now_hm outside allowed window $win. Use --force to override."
    fi
    log "idle-window ok: $now_hm within $win"
}

# ---------------------------------------------------------------------------
# dry-run plan — print intended actions, touch nothing
# ---------------------------------------------------------------------------
dry_run_plan() {
    log "[dry-run] target=$TARGET ref=$REF branch=$BRANCH"
    log "[dry-run] releases_root=$RELEASES_ROOT"
    log "[dry-run] current_link=$CURRENT_LINK"
    log "[dry-run] rollback_link=$ROLLBACK_LINK"
    log "[dry-run] release_dir=${RELEASES_ROOT}/${REF}"
    log "[dry-run] runtime_data_root=<release-local ${RELEASES_ROOT}/${REF}/data>"
    log "[dry-run] inherit_data=$INHERIT_DATA skip_frontend_build=$SKIP_FRONTEND_BUILD skip_restart=$SKIP_RESTART skip_public_check=$SKIP_PUBLIC_CHECK skip_scheduler_check=$SKIP_SCHEDULER_CHECK skip_variant_check=$SKIP_VARIANT_CHECK"
    log "[dry-run] plan: git-archive $REF -> stage -> [build frontend?] -> inherit data/reports -> manifest -> atomic switch -> [restart?] -> health -> prune"
    exit 0
}

# (preconditions + lock moved below, after all function definitions, so that
#  AQSP_DEPLOY_LIB=1 can source this file and expose the functions for tests)

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

# release-local data/ + reports/ inheritance (this repo's model)
inherit_runtime_data() {
    local new_release="$1" src
    [ "$INHERIT_DATA" = "true" ] || return 0
    if [ ! -L "$CURRENT_LINK" ]; then
        log "no current release to inherit from; new release starts with empty data/"
        return 0
    fi
    src="$(readlink -f "$CURRENT_LINK")"
    for d in data reports; do
        if [ -d "$src/$d" ]; then
            log "inherit $d from $src"
            if [ "$DRY_RUN" = "true" ]; then
                log "[dry-run] would copy $src/$d -> $new_release/$d"
                continue
            fi
            mkdir -p "$new_release/$d"
            cp -a "$src/$d/." "$new_release/$d/"
        fi
    done
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
        PREVIOUS_RELEASE="$old_current"
    fi
    ln -sfn "$release" "${CURRENT_LINK}.tmp"
    mv -Tf "${CURRENT_LINK}.tmp" "$CURRENT_LINK"
}

rollback_after_local_service_failure() {
    [ -n "$PREVIOUS_RELEASE" ] || return 1
    log "local service acceptance failed; restore previous release=$PREVIOUS_RELEASE"
    ln -sfn "$PREVIOUS_RELEASE" "${CURRENT_LINK}.tmp"
    mv -Tf "${CURRENT_LINK}.tmp" "$CURRENT_LINK"
    [ "$TARGET" = "prod" ] || return 0
    systemctl restart "$API_SERVICE"
    stop_stale_frontend_port_owner
    systemctl restart "$PREVIEW_SERVICE"
    systemctl start "$TARGET_SERVICE"
}

rollback_after_public_route_failure() {
    [ -n "$PREVIOUS_RELEASE" ] || return 1
    log "public route acceptance failed; restore previous release=$PREVIOUS_RELEASE"
    ln -sfn "$PREVIOUS_RELEASE" "${CURRENT_LINK}.tmp"
    mv -Tf "${CURRENT_LINK}.tmp" "$CURRENT_LINK"
    [ "$TARGET" = "prod" ] || return 0
    systemctl restart "$API_SERVICE"
    stop_stale_frontend_port_owner
    systemctl restart "$PREVIEW_SERVICE"
    systemctl start "$TARGET_SERVICE"
}

wait_for_local_url() {
    local url="$1" expected="$2" deadline body
    deadline=$(( $(date +%s) + LOCAL_HEALTH_TIMEOUT_SECONDS ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        body="$(curl -fsS --max-time 5 "$url" 2>/dev/null || true)"
        if printf '%s' "$body" | grep -q "$expected"; then
            return 0
        fi
        sleep 1
    done
    return 1
}

restart_services() {
    local release="$1" api_pid preview_pid api_cwd preview_cwd
    [ "$TARGET" = "prod" ] || {
        log "target=$TARGET: no systemd; release is linked, runner picks it up via PYTHONPATH on next launch"
        return 0
    }
    systemctl restart "$API_SERVICE" || return 1
    stop_stale_frontend_port_owner
    systemctl restart "$PREVIEW_SERVICE" || return 1
    systemctl start "$TARGET_SERVICE" || return 1
    sleep 4
    systemctl is-active --quiet "$API_SERVICE" || {
        echo "$API_SERVICE is not active" >&2
        return 1
    }
    systemctl is-active --quiet "$PREVIEW_SERVICE" || {
        echo "$PREVIEW_SERVICE is not active" >&2
        return 1
    }
    systemctl is-active --quiet "$TARGET_SERVICE" || {
        echo "$TARGET_SERVICE is not active" >&2
        return 1
    }
    api_pid="$(systemctl show -p MainPID --value "$API_SERVICE")"
    preview_pid="$(systemctl show -p MainPID --value "$PREVIEW_SERVICE")"
    api_cwd="$(readlink -f "/proc/$api_pid/cwd")"
    preview_cwd="$(readlink -f "/proc/$preview_pid/cwd")"
    [ "$api_cwd" = "$release/backend" ] || {
        echo "API cwd drift: $api_cwd" >&2
        return 1
    }
    [ "$preview_cwd" = "$release/frontend" ] || {
        echo "preview cwd drift: $preview_cwd" >&2
        return 1
    }
    wait_for_local_url "http://127.0.0.1:${API_PORT}/api/health" '"ok":true' || {
        echo "local API health did not become ready" >&2
        return 1
    }
    wait_for_local_url "http://127.0.0.1:${FRONTEND_PORT}/" "AQSP" || {
        echo "local frontend health did not become ready" >&2
        return 1
    }
}

check_public_routes() {
    [ "$SKIP_PUBLIC_CHECK" = "true" ] && return 0
    [ -n "$CHECK_URL" ] || return 0
    [ "$TARGET" = "prod" ] || {
        log "target=$TARGET: skipping public route check (no public endpoint)"
        return 0
    }
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
import sys
from datetime import date

sys.path.insert(0, sys.argv[2])
from aqsp.core.http import urlopen_no_macos_proxy
from aqsp.core.time import today_shanghai

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
    and universe.get("source") in {"sqlite_raw_refresh", "sqlite_raw_rebuild"}
    and isinstance(universe.get("total"), int)
    and isinstance(universe.get("resolved"), int)
    and 0 <= universe["resolved"] < universe["total"]
    and str(universe.get("batch_id") or "") == expected_end
)
is_current_intraday_snapshot = (
    selected == today_shanghai().isoformat()
    and latest_trade_date == selected
)
if expected_end and selected != expected_end and not partial_raw_refresh and not is_current_intraday_snapshot:
    raise SystemExit(f"snapshot selected_date {selected} != expected {expected_end}")
if expected_end and latest_trade_date not in {"", "未记录", expected_end, selected}:
    raise SystemExit(
        f"snapshot latest_trade_date {latest_trade_date} != expected {expected_end}"
    )
if (
    expected_end
    and latest_trade_date in {"", "未记录"}
    and not partial_raw_refresh
):
    raise SystemExit("snapshot latest_trade_date missing outside blocked raw refresh")
if partial_raw_refresh:
    print(
        "snapshot_contract blocked_incomplete_raw_data",
        f"selected_date={selected}",
        f"raw_coverage={universe['resolved']}/{universe['total']}",
    )
    raise SystemExit(0)
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
    PYTHONPATH="$RELEASE_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
    AQSP_HEADLESS_LOCK="$HEADLESS_LOCK_FILE" "$PYTHON_BIN" "$RELEASE_DIR/scripts/headless_dashboard_check.py" \
        --url "$CHECK_URL" \
        --mode raw \
        --headless-lock "$HEADLESS_LOCK_FILE" \
        --health-url "${CHECK_URL%/}/api/health"
    PYTHONPATH="$RELEASE_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
    AQSP_HEADLESS_LOCK="$HEADLESS_LOCK_FILE" "$PYTHON_BIN" "$RELEASE_DIR/scripts/headless_dashboard_check.py" \
        --url "${CHECK_URL%/}/variants" \
        --mode raw \
        --headless-lock "$HEADLESS_LOCK_FILE" \
        --health-url "${CHECK_URL%/}/api/health"
    for legacy_path in /paper-research /intel; do
        PYTHONPATH="$RELEASE_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
        AQSP_HEADLESS_LOCK="$HEADLESS_LOCK_FILE" "$PYTHON_BIN" "$RELEASE_DIR/scripts/headless_dashboard_check.py" \
            --url "${CHECK_URL%/}${legacy_path}" \
            --mode raw \
            --headless-lock "$HEADLESS_LOCK_FILE" \
            --health-url "${CHECK_URL%/}/api/health"
    done
}

check_variant_results() {
    [ "$SKIP_VARIANT_CHECK" = "true" ] && return 0
    local variant_path="${AQSP_VARIANT_RESULTS:-${RELEASE_DIR}/data/runtime/variant_results.json}"
    [ -f "$variant_path" ] || {
        log "variant results missing: $variant_path (skip)"
        return 0
    }
    local command=(
        "$PYTHON_BIN" "$RELEASE_DIR/scripts/check_variant_results.py"
        "$variant_path"
        --min-variants 24
        --min-symbols 600
    )
    if [ -n "$EXPECTED_VARIANT_END" ]; then
        command+=(--expected-end "$EXPECTED_VARIANT_END")
    fi
    PYTHONPATH="$RELEASE_DIR/src:$RELEASE_DIR/scripts" "${command[@]}"
}

cleanup_release_root_residuals() {
    # release-local model: data/ lives inside each release and is pruned with it.
    # only remove stray staging dirs / files left in RELEASES_ROOT.
    find "$RELEASES_ROOT" -mindepth 1 -maxdepth 1 -type f -delete
    find "$RELEASES_ROOT" -mindepth 1 -maxdepth 1 -type d -name '.stage-*' -exec rm -rf {} +
}

prune_releases() {
    local release="$1"
    PYTHONPATH="$release/src" "$PYTHON_BIN" "$release/scripts/check_runtime_storage.py" \
        --apply --json --env-file "$RUNTIME_ROOT/.env"
}

run_scheduler_check() {
    local release="$1"
    [ "$SKIP_SCHEDULER_CHECK" = "true" ] && return 0
    [ "$TARGET" = "prod" ] || {
        log "target=$TARGET: skipping scheduler check (no 宝塔 cron)"
        return 0
    }
    PYTHONPATH="$release/src" \
    AQSP_PROJECT_ROOT="$release" \
    AQSP_RUNTIME_ROOT="$RUNTIME_ROOT" \
    AQSP_RUNTIME_DATA_ROOT="$release/data" \
    AQSP_RUNTIME_PYTHON="$PYTHON_BIN" \
    AQSP_SCHEDULER_STRICT_SCHEDULE=true \
        "$PYTHON_BIN" "$release/scripts/check_scheduler.py"
}

# ---------------------------------------------------------------------------
# preconditions + lock (placed after function defs so AQSP_DEPLOY_LIB can
# source us and expose the functions without triggering side effects)
# ---------------------------------------------------------------------------
if [ "${AQSP_DEPLOY_LIB:-0}" = "1" ]; then
    return 0 2>/dev/null || true
fi

[ -d "$REPO_ROOT/.git" ] || fail "Git repo missing: $REPO_ROOT"
[ -x "$PYTHON_BIN" ] || fail "runtime python missing or not executable: $PYTHON_BIN"
[ -x "$NPM_BIN" ] || fail "npm missing or not executable: $NPM_BIN"

if [ "$DRY_RUN" = "true" ]; then
    dry_run_plan
fi

mkdir -p "$RELEASES_ROOT" "$LOCK_DIR"

assert_idle_window

if ! mkdir "$LOCK_FILE" 2>/dev/null; then
    fail "another immutable release deployment is running: $LOCK_FILE"
fi
STAGE_DIR=""
cleanup() {
    [ -n "$STAGE_DIR" ] && rm -rf -- "$STAGE_DIR"
    rmdir "$LOCK_FILE" 2>/dev/null || true
}
trap cleanup EXIT

COMMIT="$(resolve_commit)"
case "$COMMIT" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;; *) fail "invalid commit: $COMMIT" ;; esac
RELEASE_DIR="${RELEASES_ROOT}/${COMMIT}"
RUNTIME_DATA_ROOT="${AQSP_RUNTIME_DATA_ROOT:-${RELEASE_DIR}/data}"
log "deploy target=$TARGET commit=$COMMIT branch=$BRANCH release=$RELEASE_DIR"

if [ "$SKIP_FRONTEND_BUILD" = "true" ] && { [ ! -d "$RELEASE_DIR/frontend/node_modules" ] || [ ! -d "$RELEASE_DIR/frontend/dist" ]; }; then
    fail "--skip-frontend-build requires an existing release with complete frontend artifacts"
fi

if [ ! -d "$RELEASE_DIR" ]; then
    STAGE_DIR="$(mktemp -d "${RELEASES_ROOT}/.stage-${COMMIT}.XXXXXX")"
    (cd "$REPO_ROOT" && git archive --format=tar "$COMMIT") | tar -x -C "$STAGE_DIR"
    if [ "$SKIP_FRONTEND_BUILD" != "true" ]; then
        build_frontend "$STAGE_DIR"
    fi
    inherit_runtime_data "$STAGE_DIR"
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
    if ! restart_services "$RELEASE_DIR"; then
        rollback_after_local_service_failure || true
        fail "local API/frontend acceptance failed; previous release restore was attempted"
    fi
fi
if ! check_public_routes; then
    rollback_after_public_route_failure || true
    fail "public route acceptance failed; previous release restore was attempted"
fi
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
