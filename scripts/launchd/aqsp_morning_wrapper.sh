#!/bin/bash
# 早盘打板策略 wrapper 脚本
# 每天 09:15 北京时间由 launchd 触发
set -euo pipefail

# ============================ 环境 ============================

export LANG="en_US.UTF-8"
export TZ="Asia/Shanghai"
export PATH="/Library/Frameworks/Python.framework/Versions/3.11/bin:/usr/local/bin:/opt/homebrew/bin:$HOME/Library/Python/3.11/bin:$PATH"

PROJECT_ROOT="/Users/ivon/Documents/AI量化选股"
LOG_DIR="${PROJECT_ROOT}/logs/launchd"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/morning-$(date +%Y-%m-%d).log"

LOCK_FILE="/tmp/aqsp-morning.lock"
ERROR_FILE="/tmp/aqsp-morning-error.flag"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

cleanup() {
    rm -f "$LOCK_FILE"
}
trap cleanup EXIT

if [ -f "$LOCK_FILE" ]; then
    log "另一个实例正在运行，退出"
    exit 0
fi
touch "$LOCK_FILE"

rm -f "$ERROR_FILE"
log "===== 早盘打板策略开始 ====="

cd "$PROJECT_ROOT"

# ============================ Python ============================

VENV_DIR="${PROJECT_ROOT}/.venv"
if [ -d "$VENV_DIR" ] && [ -f "${VENV_DIR}/bin/activate" ]; then
    source "${VENV_DIR}/bin/activate"
    log "已激活虚拟环境: ${VENV_DIR}"
else
    log "虚拟环境不存在，使用系统 Python"
fi

PYTHON_BIN="${VENV_DIR:+${VENV_DIR}/bin/}python3"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3)"
fi
log "Python: ${PYTHON_BIN} ($("${PYTHON_BIN}" --version 2>&1))"

# ============================ 配置 ============================

[ -f .env ] && { set -a; source .env; set +a; }

export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"
export AQSP_MODE="morning"

# ============================ 执行 ============================

log "运行早盘打板策略..."
"${PYTHON_BIN}" -m aqsp morning-breakout --notify 2>&1 || {
    log "早盘策略运行失败 (exit $?)"
    touch "$ERROR_FILE"
    exit 1
}

log "===== 早盘打板策略完成 ====="

# 日志轮转(保留30天)
find "$LOG_DIR" -name "morning-*.log" -mtime +30 -delete 2>/dev/null || true
