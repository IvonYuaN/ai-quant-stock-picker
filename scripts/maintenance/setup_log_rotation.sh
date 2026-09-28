#!/usr/bin/env bash
# 日志轮转配置脚本
#
# 用法：
#   ./scripts/setup_log_rotation.sh [LOG_DIR]
#
# 功能：
#   - 自动检测操作系统（macOS/Linux）
#   - 配置日志轮转：按日归档、保留 30 天、自动压缩
#   - macOS: 配置 newsyslog
#   - Linux: 配置 logrotate
#
# 环境变量：
#   LOG_DIR: 日志目录（默认：$HOME/.aqsp/logs）

set -euo pipefail

# 默认日志目录
LOG_DIR="${1:-${AQSP_LOG_DIR:-$HOME/.aqsp/logs}}"

# 创建日志目录
mkdir -p "$LOG_DIR"

echo "配置日志轮转..."
echo "日志目录: $LOG_DIR"

# 检测操作系统
OS_TYPE="$(uname -s)"

case "$OS_TYPE" in
  Darwin)
    echo "检测到 macOS，配置 newsyslog..."

    # newsyslog 配置文件路径
    NEWSYSLOG_CONF="$HOME/.aqsp/newsyslog.conf"

    cat > "$NEWSYSLOG_CONF" <<EOF
# AQSP 日志轮转配置（newsyslog）
#
# 格式：logfilename [owner:group] mode count size when flags [/pid_file] [sig_num]
#
# 说明：
#   - 每日轮转（when=@T00）
#   - 保留 30 份归档（count=30）
#   - 自动压缩（flags=J）
#   - 大小超过 100M 也触发轮转（size=100）

# AQSP 主日志
$LOG_DIR/aqsp.log                644  30  100  @T00  J

# AQSP 数据源日志
$LOG_DIR/aqsp.data.log           644  30  100  @T00  J

# AQSP 后端 API 日志
$LOG_DIR/aqsp.backend.log        644  30  50   @T00  J

# AQSP 定时任务日志
$LOG_DIR/aqsp.cron.log           644  30  50   @T00  J
EOF

    echo "✓ 已生成 newsyslog 配置: $NEWSYSLOG_CONF"
    echo ""
    echo "手动轮转测试（可选）："
    echo "  newsyslog -v -f $NEWSYSLOG_CONF"
    echo ""
    echo "添加到 crontab 实现自动轮转（每天 0 点执行）："
    echo "  0 0 * * * newsyslog -f $NEWSYSLOG_CONF >/dev/null 2>&1"
    echo ""
    echo "或者使用 launchd（推荐，macOS 原生方式）："
    echo "  创建 ~/Library/LaunchAgents/com.aqsp.logrotate.plist 并配置定时任务"
    ;;

  Linux)
    echo "检测到 Linux，配置 logrotate..."

    # logrotate 配置文件路径
    LOGROTATE_CONF="$HOME/.aqsp/logrotate.conf"
    LOGROTATE_STATE="$HOME/.aqsp/logrotate.state"

    cat > "$LOGROTATE_CONF" <<EOF
# AQSP 日志轮转配置（logrotate）
#
# 手动执行：
#   logrotate -f $LOGROTATE_CONF
#
# 添加到 crontab（每天 0 点执行）：
#   0 0 * * * logrotate -s $LOGROTATE_STATE $LOGROTATE_CONF >/dev/null 2>&1

# AQSP 主日志
$LOG_DIR/aqsp.log {
    daily                    # 每日轮转
    rotate 30                # 保留 30 份
    compress                 # 压缩归档
    delaycompress            # 延迟压缩（避免当天日志被压缩）
    missingok                # 文件不存在不报错
    notifempty               # 空文件不轮转
    create 0644 $(whoami) $(whoami)  # 新建日志文件权限
    dateext                  # 文件名加日期后缀
    dateformat -%Y%m%d       # 日期格式：aqsp.log-20260928
    maxsize 100M             # 超过 100M 立即轮转
}

# AQSP 数据源日志
$LOG_DIR/aqsp.data.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 $(whoami) $(whoami)
    dateext
    dateformat -%Y%m%d
    maxsize 100M
}

# AQSP 后端 API 日志
$LOG_DIR/aqsp.backend.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 $(whoami) $(whoami)
    dateext
    dateformat -%Y%m%d
    maxsize 50M
}

# AQSP 定时任务日志
$LOG_DIR/aqsp.cron.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 $(whoami) $(whoami)
    dateext
    dateformat -%Y%m%d
    maxsize 50M
}
EOF

    # 创建 state 文件（记录上次轮转时间）
    touch "$LOGROTATE_STATE"

    echo "✓ 已生成 logrotate 配置: $LOGROTATE_CONF"
    echo "✓ 已生成 state 文件: $LOGROTATE_STATE"
    echo ""
    echo "手动轮转测试："
    echo "  logrotate -v -f $LOGROTATE_CONF"
    echo ""
    echo "添加到 crontab 实现自动轮转（每天 0 点执行）："
    echo "  (crontab -l 2>/dev/null; echo \"0 0 * * * logrotate -s $LOGROTATE_STATE $LOGROTATE_CONF >/dev/null 2>&1\") | crontab -"
    echo ""
    echo "或者使用 systemd timer（推荐，如果系统支持）："
    echo "  需要 root 权限，将配置放到 /etc/logrotate.d/ 下"
    ;;

  *)
    echo "错误：不支持的操作系统 $OS_TYPE" >&2
    echo "支持的系统：macOS (Darwin), Linux" >&2
    exit 1
    ;;
esac

echo ""
echo "✓ 日志轮转配置完成"
echo ""
echo "提示："
echo "  - 日志文件将在每天 0 点轮转"
echo "  - 保留最近 30 天的日志"
echo "  - 旧日志自动压缩（.gz）"
echo "  - 单个日志文件超过大小限制时立即轮转"
echo ""
echo "创建测试日志："
echo "  mkdir -p $LOG_DIR"
echo "  echo 'test log entry' >> $LOG_DIR/aqsp.log"
