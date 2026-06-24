#!/usr/bin/env bash
# ============================================================================
# AI量化选股项目 - 宝塔面板自动化部署脚本
# ============================================================================
# 兼容: Ubuntu 20.04/22.04, 宝塔面板 9.x
# 服务器要求: 2核2G 内存及以上
# 用途: 一键部署项目到服务器, 配置定时任务实现 24 小时自动运行
# ============================================================================

set -euo pipefail

# ============================ 配置常量 ============================

PROJECT_NAME="aqsp"
PROJECT_DIR="/opt/${PROJECT_NAME}"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON_MIN_VERSION="3.10"
GIT_REPO="${AQSP_GIT_REPO:-https://github.com/your-username/ai-quant-stock-picker.git}"
GIT_BRANCH="${AQSP_GIT_BRANCH:-main}"
LOG_FILE="/var/log/${PROJECT_NAME}-setup.log"
BACKUP_DIR="/opt/${PROJECT_NAME}-backup-$(date +%Y%m%d%H%M%S)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ============================ 工具函数 ============================

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] $1" >> "$LOG_FILE"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] $1" >> "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $1" >> "$LOG_FILE"
}

log_step() {
    echo -e "\n${BLUE}========== $1 ==========${NC}"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [STEP] $1" >> "$LOG_FILE"
}

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log_error "此脚本需要 root 权限运行"
        log_error "请使用: sudo bash $0"
        exit 1
    fi
}

check_os() {
    if [ ! -f /etc/os-release ]; then
        log_error "无法检测操作系统版本"
        exit 1
    fi

    . /etc/os-release
    if [ "$ID" != "ubuntu" ]; then
        log_warn "当前系统为 $ID $VERSION_ID, 脚本针对 Ubuntu 优化, 其他系统可能需要手动调整"
    fi

    OS_VERSION="$VERSION_ID"
    log_info "检测到操作系统: Ubuntu $OS_VERSION"
}

# ============================ 回滚机制 ============================

ROLLBACK_ACTIONS=()

add_rollback() {
    ROLLBACK_ACTIONS+=("$1")
}

execute_rollback() {
    log_warn "执行回滚操作..."
    for ((i=${#ROLLBACK_ACTIONS[@]}-1; i>=0; i--)); do
        log_warn "回滚: ${ROLLBACK_ACTIONS[$i]}"
        eval "${ROLLBACK_ACTIONS[$i]}" || true
    done
    log_warn "回滚完成"
}

trap 'if [ $? -ne 0 ]; then log_error "部署失败, 开始回滚..."; execute_rollback; fi' EXIT

# ============================ 步骤 1: 环境检测 ============================

check_environment() {
    log_step "步骤 1/7: 系统环境检测"

    check_root
    check_os

    ARCH=$(uname -m)
    log_info "系统架构: $ARCH"

    TOTAL_MEM=$(free -m | awk '/^Mem:/{print $2}')
    log_info "总内存: ${TOTAL_MEM}MB"
    if [ "$TOTAL_MEM" -lt 1800 ]; then
        log_warn "内存不足 2GB, 运行时可能出现 OOM, 建议增加 swap"
    fi

    DISK_FREE=$(df -BG /opt 2>/dev/null | awk 'NR==2{print $4}' | tr -d 'G')
    log_info "可用磁盘空间: ${DISK_FREE}G"
    if [ "${DISK_FREE:-0}" -lt 5 ]; then
        log_error "磁盘空间不足 5GB, 请清理后重试"
        exit 1
    fi

    if command -v bt &>/dev/null; then
        BT_VERSION=$(bt default 2>/dev/null | grep -oP '(\d+\.\d+\.\d+\.\d+)' | head -1 || echo "unknown")
        log_info "宝塔面板版本: $BT_VERSION"
    else
        log_warn "未检测到宝塔面板, 定时任务需手动配置 crontab"
    fi

    log_info "环境检测通过"
}

# ============================ 步骤 2: 安装系统依赖 ============================

install_system_deps() {
    log_step "步骤 2/7: 安装系统依赖"

    export DEBIAN_FRONTEND=noninteractive

    apt-get update -qq
    add_rollback "echo 'apt-get update 已执行, 无需回滚'"

    PACKAGES=(
        git
        python3
        python3-pip
        python3-venv
        python3-dev
        build-essential
        libssl-dev
        libffi-dev
        curl
        wget
        sqlite3
        libsqlite3-dev
    )

    for pkg in "${PACKAGES[@]}"; do
        if dpkg -l "$pkg" &>/dev/null; then
            log_info "$pkg 已安装"
        else
            log_info "安装 $pkg..."
            apt-get install -y -qq "$pkg"
        fi
    done

    PYTHON3_VERSION=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
    log_info "Python3 版本: $PYTHON3_VERSION"

    PYTHON3_MINOR=$(echo "$PYTHON3_VERSION" | cut -d. -f2)
    if [ "$PYTHON3_MINOR" -lt 10 ]; then
        log_warn "Python 版本 $PYTHON3_VERSION 低于最低要求 $PYTHON_MIN_VERSION"
        log_info "尝试安装 Python 3.10+..."

        if [ "$OS_VERSION" = "20.04" ]; then
            add-apt-repository -y ppa:deadsnakes/ppa
            apt-get update -qq
            apt-get install -y -qq python3.10 python3.10-venv python3.10-dev
            update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1
        else
            log_error "请手动安装 Python 3.10+ 后重试"
            exit 1
        fi
    fi

    log_info "系统依赖安装完成"
}

# ============================ 步骤 3: 克隆/更新项目代码 ============================

setup_project_code() {
    log_step "步骤 3/7: 部署项目代码"

    if [ -d "$PROJECT_DIR/.git" ]; then
        log_info "检测到已有项目目录, 备份后更新..."
        cp -r "$PROJECT_DIR" "$BACKUP_DIR"
        add_rollback "rm -rf $PROJECT_DIR && mv $BACKUP_DIR $PROJECT_DIR"

        cd "$PROJECT_DIR"
        git fetch origin
        git reset --hard "origin/${GIT_BRANCH}"
        git clean -fd
        log_info "项目代码已更新到最新版本"
    else
        if [ -d "$PROJECT_DIR" ]; then
            mv "$PROJECT_DIR" "$BACKUP_DIR"
            add_rollback "rm -rf $PROJECT_DIR && mv $BACKUP_DIR $PROJECT_DIR"
        fi

        log_info "克隆项目代码到 $PROJECT_DIR..."
        git clone --branch "$GIT_BRANCH" --depth 1 "$GIT_REPO" "$PROJECT_DIR"
        add_rollback "rm -rf $PROJECT_DIR"
        log_info "项目代码克隆完成"
    fi

    cd "$PROJECT_DIR"
    GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    log_info "当前代码版本: $GIT_COMMIT"
}

# ============================ 步骤 4: 创建虚拟环境并安装依赖 ============================

setup_python_env() {
    log_step "步骤 4/7: 配置 Python 虚拟环境"

    cd "$PROJECT_DIR"

    if [ -d "$VENV_DIR" ]; then
        log_info "检测到已有虚拟环境, 重新创建..."
        rm -rf "$VENV_DIR"
    fi

    python3 -m venv "$VENV_DIR"
    add_rollback "rm -rf $VENV_DIR"

    source "$VENV_DIR/bin/activate"

    pip install --upgrade pip setuptools wheel -q

    log_info "安装项目依赖..."
    pip install -e ".[data,dev]" -q 2>&1 | tail -5

    log_info "验证安装..."
    python3 -c "import aqsp; print('aqsp 模块导入成功')" || {
        log_error "aqsp 模块导入失败"
        exit 1
    }

    log_info "Python 虚拟环境配置完成"
    deactivate
}

# ============================ 步骤 5: 创建目录结构 ============================

setup_directories() {
    log_step "步骤 5/7: 创建目录结构"

    cd "$PROJECT_DIR"

    DIRS=(
        data
        data/snapshots
        data/cache
        logs
        logs/daily
        logs/error
        reports
        reports/archive
        config
    )

    for dir in "${DIRS[@]}"; do
        mkdir -p "$dir"
        log_info "创建目录: $dir"
    done

    touch data/.gitkeep
    touch logs/.gitkeep
    touch reports/.gitkeep

    log_info "目录结构创建完成"
}

# ============================ 步骤 6: 生成配置文件 ============================

generate_config() {
    log_step "步骤 6/7: 生成配置文件"

    cd "$PROJECT_DIR"

    ENV_FILE="$PROJECT_DIR/.env"
    if [ -f "$ENV_FILE" ]; then
        log_info ".env 文件已存在, 跳过生成"
        log_info "如需重新生成, 请先删除 .env 文件"
    else
        cat > "$ENV_FILE" << 'ENVEOF'
# ============================================================================
# AI量化选股项目 - 环境变量配置
# 生成时间: GENERATED_AT
# ============================================================================

# ---------- 标的配置 ----------
# A 股代码, 逗号分隔。留空时自动从标的池选取
AQSP_SYMBOLS=

# open: 开盘前/盘中观察池; close: 尾盘/收盘后候选池
AQSP_MODE=close

# 最终输出候选数量
AQSP_LIMIT=10

# 最大标的池数量（0=全市场）
AQSP_MAX_UNIVERSE=0

# 最低日均成交额(元)
AQSP_MIN_AVG_AMOUNT=50000000

# 是否启用在线因子(北向资金/融资融券)
AQSP_ENABLE_ONLINE_FACTORS=false

# 最大数据延迟天数
AQSP_MAX_DATA_LAG_DAYS=3

# 数据源: 生产默认使用本地 raw sqlite；公网源只能手工临时开启
AQSP_SOURCE=sqlite_db
AQSP_ALLOW_ONLINE_FALLBACK=false
AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db

# ---------- 路径配置 ----------
AQSP_LEDGER=data/predictions.jsonl
AQSP_PAPER_LEDGER=data/paper_trades.jsonl
AQSP_REPORT=reports/latest.md
AQSP_OUTPUT_CSV=reports/latest.csv
AQSP_BRIEFING_REPORT=reports/briefing.md

# ---------- 通知渠道(配置任意一个即可) ----------
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 企业微信
WECHAT_WEBHOOK_URL=

# 飞书
FEISHU_WEBHOOK_URL=

# 通用 Webhook
GENERIC_WEBHOOK_URL=

# ---------- 邮件配置(可选) ----------
AQSP_SMTP_HOST=
AQSP_SMTP_PORT=465
AQSP_SMTP_USER=
AQSP_SMTP_PASSWORD=
AQSP_SMTP_FROM=
AQSP_SMTP_TO=

# ---------- 数据源 Token(可选) ----------
TUSHARE_TOKEN=

# ---------- 服务器部署 ----------
AQSP_DEPLOY_DASHBOARD=false
AQSP_DEPLOY_HOST=
AQSP_DEPLOY_PORT=22
AQSP_DEPLOY_USER=
AQSP_DEPLOY_PATH=
AQSP_DEPLOY_SSH_KEY=

# ---------- 项目根目录(自动检测, 通常不需要修改) ----------
AQSP_PROJECT_ROOT=/opt/aqsp
ENVEOF

        sed -i "s|GENERATED_AT|$(date '+%Y-%m-%d %H:%M:%S')|g" "$ENV_FILE"
        log_info ".env 配置文件已生成"
        log_warn "请编辑 $ENV_FILE 填写通知渠道等敏感配置"
    fi

    CRON_ENV_FILE="$PROJECT_DIR/deploy/cron.env"
    mkdir -p "$PROJECT_DIR/deploy"
    cat > "$CRON_ENV_FILE" << 'CRONEOF'
# 定时任务环境变量
# cron 环境变量最小集, 确保定时任务能找到 Python 和项目路径
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PYTHONIOENCODING=utf-8
TZ=Asia/Shanghai
CRONEOF

    log_info "定时任务环境变量文件已生成"
    log_info "配置文件生成完成"
}

# ============================ 步骤 7: 配置定时任务 ============================

setup_cron_tasks() {
    log_step "步骤 7/7: 配置定时任务"

    if [ "${AQSP_INSTALL_SYSTEM_CRON:-false}" != "true" ]; then
        log_info "默认不写系统 crontab，避免与宝塔计划任务形成双入口"
        log_info "如需使用系统 crontab，显式设置 AQSP_INSTALL_SYSTEM_CRON=true 后重跑"
        return
    fi

    BT_TASK="$PROJECT_DIR/scripts/bt_task.sh"
    CRON_LOG="$PROJECT_DIR/logs/cron.log"

    CRON_ENTRY="0 18 * * 1-5 /bin/bash ${BT_TASK} daily >> ${CRON_LOG} 2>&1"

    if crontab -l 2>/dev/null | grep -qF "daily_pipeline.sh"; then
        log_info "定时任务已存在, 更新..."
        crontab -l 2>/dev/null | grep -vF "daily_pipeline.sh" | crontab -
    fi
    if crontab -l 2>/dev/null | grep -qF "bt_task.sh daily"; then
        log_info "统一定时任务已存在, 更新..."
        crontab -l 2>/dev/null | grep -vF "bt_task.sh daily" | crontab -
    fi

    (crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
    add_rollback "crontab -l 2>/dev/null | grep -vF 'bt_task.sh daily' | crontab -"

    log_info "定时任务已配置: 每周一至周五 18:00(北京时间)运行统一 daily 入口"
    log_info "查看定时任务: crontab -l"
}

# ============================ 部署完成信息 ============================

print_completion() {
    log_step "部署完成"

    echo ""
    echo -e "${GREEN}============================================================================${NC}"
    echo -e "${GREEN}  AI量化选股项目部署完成!${NC}"
    echo -e "${GREEN}============================================================================${NC}"
    echo ""
    echo -e "  项目目录: ${BLUE}${PROJECT_DIR}${NC}"
    echo -e "  虚拟环境: ${BLUE}${VENV_DIR}${NC}"
    echo -e "  配置文件: ${BLUE}${PROJECT_DIR}/.env${NC}"
    echo -e "  日志目录: ${BLUE}${PROJECT_DIR}/logs${NC}"
    echo -e "  报告目录: ${BLUE}${PROJECT_DIR}/reports${NC}"
    echo ""
    echo -e "${YELLOW}  下一步操作:${NC}"
    echo ""
    echo -e "  1. 编辑配置文件, 填写通知渠道:"
    echo -e "     ${BLUE}vi ${PROJECT_DIR}/.env${NC}"
    echo ""
    echo -e "  2. 手动测试运行:"
    echo -e "     ${BLUE}cd ${PROJECT_DIR}${NC}"
    echo -e "     ${BLUE}source .venv/bin/activate${NC}"
    echo -e "     ${BLUE}python -m aqsp run --dry-run${NC}"
    echo ""
    echo -e "  3. 查看定时任务:"
    echo -e "     ${BLUE}crontab -l${NC}"
    echo ""
    echo -e "  4. 查看运行日志:"
    echo -e "     ${BLUE}tail -f ${PROJECT_DIR}/logs/daily/\$(date +%Y-%m-%d).log${NC}"
    echo ""
    echo -e "  5. 宝塔面板配置(可选):"
    echo -e "     参考 ${BLUE}${PROJECT_DIR}/deploy/bt_panel_setup.md${NC}"
    echo ""
    echo -e "${GREEN}============================================================================${NC}"
}

# ============================ 主流程 ============================

main() {
    echo ""
    echo -e "${BLUE}============================================================================${NC}"
    echo -e "${BLUE}  AI量化选股项目 - 宝塔面板自动化部署${NC}"
    echo -e "${BLUE}  目标目录: /opt/aqsp${NC}"
    echo -e "${BLUE}============================================================================${NC}"
    echo ""

    mkdir -p "$(dirname "$LOG_FILE")"
    touch "$LOG_FILE"

    log_info "部署开始, 日志文件: $LOG_FILE"

    check_environment
    install_system_deps
    setup_project_code
    setup_python_env
    setup_directories
    generate_config
    setup_cron_tasks
    print_completion

    trap - EXIT
    log_info "部署流程全部完成"
}

main "$@"
