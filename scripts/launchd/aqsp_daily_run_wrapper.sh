#!/usr/bin/env bash
# LaunchAgent entrypoint. Keep a copy at ~/.aqsp/aqsp_daily_run_wrapper.sh
# because macOS may block launchd from executing scripts under Documents.
set -e

PROJECT_ROOT="/Users/ivon/Documents/AI量化选股"
cd "$PROJECT_ROOT"

export PATH="/Library/Frameworks/Python.framework/Versions/3.11/bin:/usr/local/bin:/opt/homebrew/bin:$HOME/Library/Python/3.11/bin:$PATH"
export PYTHONPATH="$PROJECT_ROOT/src:$PROJECT_ROOT:${PYTHONPATH:-}"

exec /bin/bash --login "$PROJECT_ROOT/scripts/daily_run.sh"
