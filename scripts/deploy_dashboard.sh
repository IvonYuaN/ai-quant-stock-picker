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
SSH_OPTS="-p $PORT -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=$SSH_CONNECT_TIMEOUT -o ConnectionAttempts=1 -o ServerAliveInterval=5 -o ServerAliveCountMax=2"
if [ -f "$SSH_KEY_PATH" ]; then
    SSH_OPTS="-i $SSH_KEY_PATH $SSH_OPTS"
fi

if [ ! -d "$SOURCE_DIR" ]; then
    echo "missing dashboard dir: $SOURCE_DIR" >&2
    exit 1
fi

ssh $SSH_OPTS \
    "$AQSP_DEPLOY_USER@$AQSP_DEPLOY_HOST" \
    "mkdir -p '$AQSP_DEPLOY_PATH'"

rsync -az --delete \
    -e "ssh $SSH_OPTS" \
    "$SOURCE_DIR"/ \
    "$AQSP_DEPLOY_USER@$AQSP_DEPLOY_HOST:$AQSP_DEPLOY_PATH"/

echo "deployed $SOURCE_DIR -> $AQSP_DEPLOY_USER@$AQSP_DEPLOY_HOST:$AQSP_DEPLOY_PATH"
