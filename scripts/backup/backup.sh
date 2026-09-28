#!/usr/bin/env bash
# 数据备份脚本 - 增量备份关键数据到本地或远程位置
#
# 功能：
# - 增量备份 data/*.jsonl, reports/, config/ 目录
# - 自动压缩并添加时间戳
# - 保留最近 N 个版本，自动清理旧备份
# - 支持本地和远程备份（rsync over SSH）
# - 完整的日志记录和错误处理
#
# 用法：
#   backup.sh [OPTIONS]
#
# 选项：
#   --backup-dir DIR       备份目标目录（默认: $PROJECT_ROOT/backups）
#   --remote USER@HOST:PATH 远程备份位置（可选）
#   --keep-versions N      保留最近 N 个版本（默认: 10）
#   --compress-level N     压缩级别 0-9（默认: 6）
#   --dry-run              演练模式，不实际执行
#   --help                 显示此帮助信息
#
# 环境变量：
#   AQSP_PROJECT_ROOT      项目根目录
#   AQSP_BACKUP_DIR        备份目录
#   AQSP_BACKUP_REMOTE     远程备份位置
#   AQSP_BACKUP_KEEP       保留版本数
#   AQSP_BACKUP_LOG_DIR    日志目录

set -euo pipefail

# ============================================================================
# 配置和初始化
# ============================================================================

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_DIR="${AQSP_BACKUP_DIR:-${PROJECT_ROOT}/backups}"
BACKUP_REMOTE="${AQSP_BACKUP_REMOTE:-}"
KEEP_VERSIONS="${AQSP_BACKUP_KEEP:-10}"
COMPRESS_LEVEL=6
DRY_RUN=false
LOG_DIR="${AQSP_BACKUP_LOG_DIR:-${PROJECT_ROOT}/logs/backup}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_NAME="backup-${TIMESTAMP}"

# 需要备份的目录和文件
BACKUP_SOURCES=(
    "data/predictions.jsonl"
    "data/paper_trades.jsonl"
    "data/ledger.jsonl"
    "data/weight_history.jsonl"
    "data/pick_snapshots.jsonl"
    "data/debate_results.jsonl"
    "data/llm_calls.jsonl"
    "reports"
    "config"
)

# ============================================================================
# 工具函数
# ============================================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $1" >&2 | tee -a "$LOG_FILE"
}

show_help() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
    exit 0
}

human_size() {
    local size=$1
    if command -v numfmt >/dev/null 2>&1; then
        numfmt --to=iec-i --suffix=B "$size"
    else
        awk -v size="$size" 'BEGIN {
            units[0]="B"; units[1]="KiB"; units[2]="MiB"; units[3]="GiB"; units[4]="TiB"
            i=0
            while(size>=1024 && i<4) { size/=1024; i++ }
            printf "%.2f%s\n", size, units[i]
        }'
    fi
}

# ============================================================================
# 参数解析
# ============================================================================

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup-dir)
            BACKUP_DIR="$2"
            shift 2
            ;;
        --remote)
            BACKUP_REMOTE="$2"
            shift 2
            ;;
        --keep-versions)
            KEEP_VERSIONS="$2"
            shift 2
            ;;
        --compress-level)
            COMPRESS_LEVEL="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            show_help
            ;;
        *)
            log_error "未知选项: $1"
            echo "使用 --help 查看帮助信息" >&2
            exit 1
            ;;
    esac
done

# ============================================================================
# 验证配置
# ============================================================================

if ! [[ "$KEEP_VERSIONS" =~ ^[0-9]+$ ]] || [ "$KEEP_VERSIONS" -lt 1 ]; then
    log_error "KEEP_VERSIONS 必须是正整数: $KEEP_VERSIONS"
    exit 2
fi

if ! [[ "$COMPRESS_LEVEL" =~ ^[0-9]$ ]]; then
    log_error "COMPRESS_LEVEL 必须是 0-9 之间的数字: $COMPRESS_LEVEL"
    exit 2
fi

# ============================================================================
# 准备备份环境
# ============================================================================

mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/backup-${TIMESTAMP}.log"

log "=== 开始备份 @ $(date) ==="
log "项目根目录: $PROJECT_ROOT"
log "备份目录: $BACKUP_DIR"
log "备份名称: $BACKUP_NAME"
log "保留版本数: $KEEP_VERSIONS"
log "压缩级别: $COMPRESS_LEVEL"
log "演练模式: $DRY_RUN"

if [ -n "$BACKUP_REMOTE" ]; then
    log "远程备份: $BACKUP_REMOTE"
fi

if [ "$DRY_RUN" = true ]; then
    log "[DRY RUN] 演练模式，不会实际执行备份操作"
fi

# 创建备份目录结构
if [ "$DRY_RUN" = false ]; then
    mkdir -p "$BACKUP_DIR"
    mkdir -p "${BACKUP_DIR}/.tmp"
fi

# ============================================================================
# 执行增量备份
# ============================================================================

TEMP_DIR="${BACKUP_DIR}/.tmp/${BACKUP_NAME}"
BACKUP_MANIFEST="${TEMP_DIR}/manifest.txt"
BACKUP_METADATA="${TEMP_DIR}/metadata.env"

log "创建临时备份目录: $TEMP_DIR"
if [ "$DRY_RUN" = false ]; then
    mkdir -p "$TEMP_DIR"
fi

# 生成备份清单
log "生成备份清单..."
file_count=0
total_size=0

for source in "${BACKUP_SOURCES[@]}"; do
    source_path="${PROJECT_ROOT}/${source}"

    if [ ! -e "$source_path" ]; then
        log "跳过不存在的源: $source"
        continue
    fi

    log "备份: $source"

    if [ "$DRY_RUN" = false ]; then
        # 使用 rsync 进行增量备份
        rsync -a --stats \
            --out-format='%n' \
            "$source_path" \
            "${TEMP_DIR}/" \
            2>&1 | tee -a "$LOG_FILE" | grep -v '^$' || true

        # 统计文件信息
        if [ -f "$source_path" ]; then
            file_count=$((file_count + 1))
            size=$(stat -f %z "$source_path" 2>/dev/null || stat -c %s "$source_path")
            total_size=$((total_size + size))
            echo "$source|file|$size" >> "$BACKUP_MANIFEST"
        elif [ -d "$source_path" ]; then
            count=$(find "$source_path" -type f | wc -l | tr -d ' ')
            file_count=$((file_count + count))
            size=$(du -sb "$source_path" 2>/dev/null | cut -f1 || du -sk "$source_path" | cut -f1)
            total_size=$((total_size + size))
            echo "$source|dir|$size|$count" >> "$BACKUP_MANIFEST"
        fi
    else
        log "[DRY RUN] 将备份: $source"
    fi
done

log "备份文件数: $file_count"
log "备份总大小: $(human_size $total_size)"

# ============================================================================
# 生成元数据
# ============================================================================

if [ "$DRY_RUN" = false ]; then
    cat > "$BACKUP_METADATA" <<EOF
BACKUP_NAME=${BACKUP_NAME}
BACKUP_TIMESTAMP=${TIMESTAMP}
BACKUP_DATE=$(date '+%Y-%m-%d %H:%M:%S')
PROJECT_ROOT=${PROJECT_ROOT}
FILE_COUNT=${file_count}
TOTAL_SIZE=${total_size}
HOSTNAME=$(hostname)
USER=$(whoami)
EOF

    log "元数据已保存到: $BACKUP_METADATA"
fi

# ============================================================================
# 压缩备份
# ============================================================================

BACKUP_ARCHIVE="${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"

log "压缩备份到: $BACKUP_ARCHIVE"
if [ "$DRY_RUN" = false ]; then
    tar -czf "$BACKUP_ARCHIVE" \
        --options gzip:compression-level=$COMPRESS_LEVEL \
        -C "${BACKUP_DIR}/.tmp" \
        "$BACKUP_NAME" \
        2>&1 | tee -a "$LOG_FILE"

    archive_size=$(stat -f %z "$BACKUP_ARCHIVE" 2>/dev/null || stat -c %s "$BACKUP_ARCHIVE")
    log "压缩完成: $(human_size $archive_size)"
    log "压缩率: $(awk -v orig=$total_size -v comp=$archive_size 'BEGIN {printf "%.1f%%", (1-comp/orig)*100}')"

    # 清理临时目录
    rm -rf "$TEMP_DIR"
fi

# ============================================================================
# 远程备份（可选）
# ============================================================================

if [ -n "$BACKUP_REMOTE" ] && [ "$DRY_RUN" = false ]; then
    log "同步到远程备份位置: $BACKUP_REMOTE"

    if rsync -avz --progress \
        "$BACKUP_ARCHIVE" \
        "$BACKUP_REMOTE/" \
        2>&1 | tee -a "$LOG_FILE"; then
        log "远程备份成功"
    else
        log_error "远程备份失败，但本地备份已完成"
    fi
fi

# ============================================================================
# 清理旧备份
# ============================================================================

log "清理旧备份（保留最近 $KEEP_VERSIONS 个版本）..."

if [ "$DRY_RUN" = false ]; then
    backup_count=$(find "$BACKUP_DIR" -maxdepth 1 -name "backup-*.tar.gz" -type f | wc -l | tr -d ' ')
    log "当前备份总数: $backup_count"

    if [ "$backup_count" -gt "$KEEP_VERSIONS" ]; then
        to_remove=$((backup_count - KEEP_VERSIONS))
        log "需要删除 $to_remove 个旧备份"

        find "$BACKUP_DIR" -maxdepth 1 -name "backup-*.tar.gz" -type f -print0 | \
            xargs -0 ls -t | \
            tail -n "+$((KEEP_VERSIONS + 1))" | \
            while IFS= read -r old_backup; do
                old_size=$(stat -f %z "$old_backup" 2>/dev/null || stat -c %s "$old_backup")
                log "删除旧备份: $(basename "$old_backup") ($(human_size $old_size))"
                rm -f "$old_backup"
            done
    else
        log "备份数量未超过保留限制，无需清理"
    fi
fi

# ============================================================================
# 清理远程旧备份（可选）
# ============================================================================

if [ -n "$BACKUP_REMOTE" ] && [ "$DRY_RUN" = false ]; then
    log "清理远程旧备份..."

    # 提取远程主机和路径
    remote_host="${BACKUP_REMOTE%%:*}"
    remote_path="${BACKUP_REMOTE#*:}"

    ssh "$remote_host" "
        cd '$remote_path' && \
        backup_count=\$(find . -maxdepth 1 -name 'backup-*.tar.gz' -type f | wc -l) && \
        if [ \"\$backup_count\" -gt $KEEP_VERSIONS ]; then \
            find . -maxdepth 1 -name 'backup-*.tar.gz' -type f -print0 | \
                xargs -0 ls -t | \
                tail -n \"+$((KEEP_VERSIONS + 1))\" | \
                xargs rm -f && \
            echo '已清理远程旧备份'; \
        else \
            echo '远程备份数量未超过限制'; \
        fi
    " 2>&1 | tee -a "$LOG_FILE" || log_error "清理远程备份失败"
fi

# ============================================================================
# 备份完成报告
# ============================================================================

log "=== 备份完成 @ $(date) ==="
log "备份文件: $BACKUP_ARCHIVE"
log "日志文件: $LOG_FILE"

if [ "$DRY_RUN" = true ]; then
    log "[DRY RUN] 演练模式完成，未实际执行备份"
    exit 0
fi

# 验证备份完整性
if [ -f "$BACKUP_ARCHIVE" ]; then
    log "验证备份完整性..."
    if tar -tzf "$BACKUP_ARCHIVE" >/dev/null 2>&1; then
        log "✓ 备份文件完整性验证通过"
    else
        log_error "✗ 备份文件完整性验证失败"
        exit 3
    fi
else
    log_error "✗ 备份文件不存在"
    exit 3
fi

log "备份成功完成"
exit 0
