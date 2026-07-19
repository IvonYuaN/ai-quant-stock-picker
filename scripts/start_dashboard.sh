#!/usr/bin/env bash
# Compatibility entry point. Production and local preview both use AQSP.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${PROJECT_ROOT}/scripts/start_vibe_research.sh" "$@"
