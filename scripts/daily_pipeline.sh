#!/usr/bin/env bash
# ============================================================================
# AI量化选股 - 每日跑批 Shell 包装脚本
# ============================================================================
# 用途: 作为定时任务(cron)的入口脚本
# 功能: 激活虚拟环境 -> 加载配置 -> 运行 Python 跑批 -> 记录日志 -> 发送通知
# 调度: 建议周一至周五凌晨 2:00(北京时间)由 cron 触发
# ============================================================================

set -euo pipefail

# ============================ 配置 ============================

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python3"
PIPELINE_SCRIPT="${PROJECT_ROOT}/scripts/daily_pipeline.py"
LOG_DIR="${PROJECT_ROOT}/logs/daily"
RESULT_LOG="${LOG_DIR}/pipeline-$(date +%Y-%m-%d).log"

# ============================ 工具函数 ============================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RESULT_LOG"
}

# ============================ 前置检查 ============================

mkdir -p "$LOG_DIR"

if [ ! -d "$VENV_DIR" ]; then
    log "[ERROR] 虚拟环境不存在: $VENV_DIR"
    log "[ERROR] 请先运行 deploy/setup.sh 部署项目"
    exit 1
fi

if [ ! -f "$PYTHON_BIN" ]; then
    log "[ERROR] Python 可执行文件不存在: $PYTHON_BIN"
    exit 1
fi

if [ ! -f "$PIPELINE_SCRIPT" ]; then
    log "[ERROR] 跑批脚本不存在: $PIPELINE_SCRIPT"
    exit 1
fi

# ============================ 加载环境变量 ============================

# 加载 .env 文件
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
    log "已加载 .env 配置"
fi

# 设置 PYTHONPATH
export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH:-}"

# 设置时区
export TZ="${TZ:-Asia/Shanghai}"

# ============================ 周末跳过检查 ============================

DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
    log "周末(周${DOW}), 跳过跑批"
    exit 0
fi

# ============================ 运行跑批 ============================

log "=========================================="
log "AI量化选股 - 每日跑批开始"
log "项目目录: ${PROJECT_ROOT}"
log "Python: ${PYTHON_BIN}"
log "=========================================="

# 记录开始时间
START_TIME=$(date +%s)

# 运行 Python 跑批脚本
set +e
"${PYTHON_BIN}" "${PIPELINE_SCRIPT}" \
    --project-root "${PROJECT_ROOT}" \
    --source "${AQSP_SOURCE:-auto}" \
    "$@" 2>&1 | tee -a "$RESULT_LOG"
PIPELINE_EXIT_CODE=${PIPELINE_EXIT_CODE:-$?}
set -e

# 记录结束时间
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

# ============================ 结果处理 ============================

log ""
log "=========================================="
log "跑批结束"
log "退出码: ${PIPELINE_EXIT_CODE}"
log "耗时: ${DURATION}秒"
log "=========================================="

case ${PIPELINE_EXIT_CODE} in
    0)
        log "✓ 跑批成功完成"
        ;;
    1)
        log "⚠ 跑批完成但有步骤失败"
        ;;
    2)
        log "✗ 跑批异常终止"
        ;;
    130)
        log "⚠ 用户中断"
        ;;
    *)
        log "✗ 未知退出码: ${PIPELINE_EXIT_CODE}"
        ;;
esac

# ============================ 部署Dashboard到服务器 ============================

if [ "${AQSP_DEPLOY_DASHBOARD:-false}" = "true" ]; then
    log "正在部署 Dashboard 到服务器..."
    DEPLOY_SCRIPT="${PROJECT_ROOT}/scripts/deploy_dashboard.sh"
    DASHBOARD_DIR="${PROJECT_ROOT}/dist/dashboard"

    if [ -f "$DEPLOY_SCRIPT" ] && [ -d "$DASHBOARD_DIR" ]; then
        if bash "$DEPLOY_SCRIPT" "$DASHBOARD_DIR" 2>&1 | tee -a "$RESULT_LOG"; then
            log "✓ Dashboard 部署成功"
        else
            log "⚠ Dashboard 部署失败（不影响主流程）"
        fi
    else
        log "⚠ 部署脚本或Dashboard目录不存在，跳过部署"
    fi
else
    log "Dashboard 部署已禁用（设置 AQSP_DEPLOY_DASHBOARD=true 启用）"
fi

# ============================ 数据生命周期管理 ============================

# 每周日凌晨执行数据清理
if [ "$DOW" -eq 7 ] || [ "${AQSP_FORCE_CLEANUP:-false}" = "true" ]; then
    log "执行数据生命周期管理..."
    CLEANUP_SCRIPT="${PROJECT_ROOT}/scripts/manage_data_lifecycle.py"

    if [ -f "$CLEANUP_SCRIPT" ]; then
        "${PYTHON_BIN}" "$CLEANUP_SCRIPT" --auto 2>&1 | tee -a "$RESULT_LOG" || true
        log "数据清理完成"
    fi
fi

# ============================ 日志轮转(保留30天) ============================

find "$LOG_DIR" -name "pipeline-*.log" -mtime +30 -delete 2>/dev/null || true

# ============================ 最终状态 ============================

log "日志文件: ${RESULT_LOG}"
log ""

exit ${PIPELINE_EXIT_CODE}
