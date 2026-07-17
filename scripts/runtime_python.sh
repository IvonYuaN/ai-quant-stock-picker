#!/usr/bin/env bash
# Shared runtime selection for scheduled AQSP tasks.
# Precedence: explicit interpreter, explicit release venv, then the installed
# Vibe-Research venv, and finally the legacy .venv fallback.

aqsp_runtime_python() {
    local project_root="${1:?project root is required}"
    local configured="${AQSP_PYTHON:-}"
    local venv_dir="${AQSP_RUNTIME_VENV_DIR:-${AQSP_VIBE_VENV_DIR:-${AQSP_INTRADAY_VENV_DIR:-}}}"

    if [ -n "$configured" ]; then
        printf '%s\n' "$configured"
        return 0
    fi
    if [ -n "$venv_dir" ]; then
        printf '%s/bin/python3\n' "$venv_dir"
        return 0
    fi
    if [ -x "${project_root}/.venv-vibe-research/bin/python3" ]; then
        printf '%s\n' "${project_root}/.venv-vibe-research/bin/python3"
        return 0
    fi
    printf '%s\n' "${project_root}/.venv/bin/python3"
}

aqsp_require_runtime_python() {
    local python_bin="$1"
    if [ ! -x "$python_bin" ]; then
        echo "[ERROR] AQSP runtime Python 不存在或不可执行: ${python_bin}" >&2
        echo "[ERROR] 请设置 AQSP_PYTHON 或 AQSP_RUNTIME_VENV_DIR，并确保它与当前 release 一致" >&2
        return 1
    fi
}
