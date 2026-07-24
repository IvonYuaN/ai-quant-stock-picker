#!/usr/bin/env bash
# Deploy generated AQSP dashboard artifacts to a private server over SSH.
set -euo pipefail

: "${AQSP_DEPLOY_HOST:?AQSP_DEPLOY_HOST is required}"
: "${AQSP_DEPLOY_USER:?AQSP_DEPLOY_USER is required}"
: "${AQSP_DEPLOY_PATH:?AQSP_DEPLOY_PATH is required}"

PORT="${AQSP_DEPLOY_PORT:-22}"
SOURCE_DIR="${1:-dist/dashboard}"
SSH_KEY_PATH="${AQSP_DEPLOY_SSH_KEY_PATH:-$HOME/.ssh/aqsp_deploy}"
SSH_CONNECT_TIMEOUT="${AQSP_DEPLOY_CONNECT_TIMEOUT_SECONDS:-8}"
TRANSFER_TIMEOUT="${AQSP_DEPLOY_TRANSFER_TIMEOUT_SECONDS:-120}"
TLS_URL="${AQSP_DEPLOY_TLS_URL:-${AQSP_DEPLOY_HEALTH_URL:-}}"
TLS_TIMEOUT="${AQSP_DEPLOY_TLS_TIMEOUT_SECONDS:-10}"
VERIFY_URLS_RAW="${AQSP_DEPLOY_VERIFY_URLS:-$TLS_URL}"

SSH_OPTS=(
    -p "$PORT"
    -o StrictHostKeyChecking=accept-new
    -o BatchMode=yes
    -o "ConnectTimeout=$SSH_CONNECT_TIMEOUT"
    -o ConnectionAttempts=1
    -o ServerAliveInterval=5
    -o ServerAliveCountMax=2
)
if [ -f "$SSH_KEY_PATH" ]; then
    SSH_OPTS=(-i "$SSH_KEY_PATH" "${SSH_OPTS[@]}")
fi

fail() {
    printf 'dashboard deployment failed: %s\n' "$1" >&2
    exit 1
}

verify_urls() {
    local phase="$1"
    local url
    local -a urls
    IFS=',' read -r -a urls <<< "$VERIFY_URLS_RAW"
    for url in "${urls[@]}"; do
        url="${url//[[:space:]]/}"
        [ -z "$url" ] && continue
        case "$url" in
            https://*) ;;
            *)
                printf 'deployment verification URL must use https://: %s\n' "$url" >&2
                return 1
                ;;
        esac
        if ! curl --fail --silent --show-error \
            --connect-timeout "$TLS_TIMEOUT" --max-time "$TLS_TIMEOUT" \
            "$url" >/dev/null; then
            printf '%s verification failed: %s\n' "$phase" "$url" >&2
            return 1
        fi
    done
}

remote_quote() {
    local value="$1"
    printf "'%s'" "${value//\'/\'\\\'\'}"
}

hash_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

validate_remote_path() {
    case "$1" in
        /*) ;;
        *) fail "AQSP_DEPLOY_PATH must be absolute" ;;
    esac
    case "$1" in
        /) fail "AQSP_DEPLOY_PATH must not be the filesystem root" ;;
        *[!A-Za-z0-9_./-]*) fail "AQSP_DEPLOY_PATH contains unsupported characters" ;;
    esac
}

validate_remote_path "$AQSP_DEPLOY_PATH"
case "$SOURCE_DIR" in
    /*) ;;
    *) SOURCE_DIR="$(pwd)/$SOURCE_DIR" ;;
esac

if [ ! -d "$SOURCE_DIR" ]; then
    fail "missing dashboard dir: $SOURCE_DIR"
fi
if ! find "$SOURCE_DIR" -type f -print -quit | grep -q .; then
    fail "dashboard dir is empty: $SOURCE_DIR"
fi

if [ -n "$VERIFY_URLS_RAW" ]; then
    verify_urls "pre-deploy" || fail "pre-deploy verification failed"
elif [ "${AQSP_DEPLOY_REQUIRE_TLS:-false}" = "true" ]; then
    fail "AQSP_DEPLOY_REQUIRE_TLS=true requires AQSP_DEPLOY_VERIFY_URLS or AQSP_DEPLOY_TLS_URL"
fi

REMOTE_PARENT="$(dirname "$AQSP_DEPLOY_PATH")"
REMOTE_TARGET="$AQSP_DEPLOY_USER@$AQSP_DEPLOY_HOST"
REMOTE_PARENT_Q="$(remote_quote "$REMOTE_PARENT")"
REMOTE_ACTIVE_Q="$(remote_quote "$AQSP_DEPLOY_PATH")"
REMOTE_STAGE=""
REMOTE_STAGE_Q=""
MANIFEST_FILE=""

cleanup() {
    if [ -n "$REMOTE_STAGE" ]; then
        ssh "${SSH_OPTS[@]}" "$REMOTE_TARGET" \
            "rm -rf $(remote_quote "$REMOTE_STAGE")" >/dev/null 2>&1 || true
    fi
    if [ -n "$MANIFEST_FILE" ]; then
        rm -f "$MANIFEST_FILE"
    fi
}
trap cleanup EXIT

if ! REMOTE_STAGE="$(ssh "${SSH_OPTS[@]}" "$REMOTE_TARGET" \
    "set -eu; mkdir -p $REMOTE_PARENT_Q; mktemp -d ${REMOTE_PARENT_Q}/.aqsp-dashboard.XXXXXX")"; then
    fail "SSH preflight failed; active directory was not changed"
fi
case "$REMOTE_STAGE" in
    "$REMOTE_PARENT"/.aqsp-dashboard.*) ;;
    *) fail "remote temporary directory was invalid: $REMOTE_STAGE" ;;
esac
REMOTE_STAGE_Q="$(remote_quote "$REMOTE_STAGE")"

MANIFEST_FILE="$(mktemp "${TMPDIR:-/tmp}/aqsp-dashboard-manifest.XXXXXX")"
(
    cd "$SOURCE_DIR"
    LC_ALL=C find . -type f -print | LC_ALL=C sort | while IFS= read -r relative; do
        hash="$(hash_file "$relative")"
        printf '%s  %s\n' "$hash" "$relative"
    done
) > "$MANIFEST_FILE"

rsync -az --checksum --delete --timeout="$TRANSFER_TIMEOUT" \
    -e "$(printf 'ssh %q ' "${SSH_OPTS[@]}")" \
    "$SOURCE_DIR"/ \
    "$REMOTE_TARGET:$REMOTE_STAGE"/

rsync -az --checksum --timeout="$TRANSFER_TIMEOUT" \
    -e "$(printf 'ssh %q ' "${SSH_OPTS[@]}")" \
    "$MANIFEST_FILE" \
    "$REMOTE_TARGET:$REMOTE_STAGE/.aqsp-manifest.sha256"

VERIFY_COMMAND="set -eu
cd $REMOTE_STAGE_Q
test -s .aqsp-manifest.sha256
expected_files=\$(awk 'NF { count += 1 } END { print count + 0 }' .aqsp-manifest.sha256)
actual_files=\$(find . -type f ! -name .aqsp-manifest.sha256 | wc -l | tr -d ' ')
test \"\$expected_files\" -eq \"\$actual_files\"
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c .aqsp-manifest.sha256
elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -c .aqsp-manifest.sha256
else
    printf 'no SHA-256 verifier on remote host\\n' >&2
    exit 1
fi"
if ! ssh "${SSH_OPTS[@]}" "$REMOTE_TARGET" "$VERIFY_COMMAND"; then
    fail "remote file/hash verification failed; active directory was not changed"
fi

ACTIVATE_COMMAND="set -eu
active=$REMOTE_ACTIVE_Q
stage=$REMOTE_STAGE_Q
backup=\${active}.aqsp-previous.\$\$
had_active=0
if [ -e \"\$backup\" ] || [ -L \"\$backup\" ]; then
    printf 'rollback path already exists: %s\\n' \"\$backup\" >&2
    exit 1
fi
if [ -e \"\$active\" ] || [ -L \"\$active\" ]; then
    mv \"\$active\" \"\$backup\"
    had_active=1
fi
restore() {
    if [ -e \"\$active\" ] || [ -L \"\$active\" ]; then
        rm -rf \"\$active\"
    fi
    if [ \"\$had_active\" -eq 1 ]; then
        mv \"\$backup\" \"\$active\"
    fi
}
if ! mv \"\$stage\" \"\$active\"; then
    restore
    exit 1
fi
if ! test -f \"\$active/.aqsp-manifest.sha256\"; then
    restore
    exit 1
fi
if ! rm -f \"\$active/.aqsp-manifest.sha256\"; then
    restore
    exit 1
fi
printf '%s\\n' \"\$backup\""

if ! REMOTE_BACKUP="$(ssh "${SSH_OPTS[@]}" "$REMOTE_TARGET" "$ACTIVATE_COMMAND")"; then
    fail "atomic activation failed; remote rollback was attempted"
fi
REMOTE_STAGE=""

if [ -n "$VERIFY_URLS_RAW" ]; then
    if ! verify_urls "post-deploy"; then
        ROLLBACK_COMMAND="set -eu
active=$REMOTE_ACTIVE_Q
backup=$(remote_quote "$REMOTE_BACKUP")
failed=\${active}.aqsp-failed.\$\$
if [ ! -e \"\$backup\" ] && [ ! -L \"\$backup\" ]; then
    printf 'rollback backup is missing: %s\\n' \"\$backup\" >&2
    exit 1
fi
if [ -e \"\$active\" ] || [ -L \"\$active\" ]; then
    mv \"\$active\" \"\$failed\"
fi
mv \"\$backup\" \"\$active\"
rm -rf \"\$failed\""
        if ssh "${SSH_OPTS[@]}" "$REMOTE_TARGET" "$ROLLBACK_COMMAND"; then
            fail "TLS post-deploy check failed; previous dashboard restored"
        fi
        fail "TLS post-deploy check failed and remote rollback could not be confirmed; backup retained at $REMOTE_BACKUP"
    fi
fi

printf 'deployed %s -> %s:%s (previous version retained at %s)\n' \
    "$SOURCE_DIR" "$REMOTE_TARGET" "$AQSP_DEPLOY_PATH" "$REMOTE_BACKUP"
