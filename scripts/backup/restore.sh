#!/usr/bin/env bash
# 数据恢复脚本 - 从备份恢复数据
#
# 功能：
# - 列出所有可用备份版本
# - 支持指定版本恢复或最新版本恢复
# - 恢复前验证备份完整性
# - 创建恢复点，支持回滚
# - 完整的日志记录和错误处理
# - 交互式确认机制
#
# 用法：
#   restore.sh [OPTIONS]
#
# 选项：
#   --list                 列出所有可用备份
#   --backup FILE          指定要恢复的备份文件
#   --latest               恢复最新备份
#   --backup-dir DIR       备份目录（默认: $PROJECT_ROOT/backups）
#   --restore-point NAME   恢复点名称（默认: 自动生成）
#   --skip-restore-point   跳过创建恢复点
#   --force                强制恢复，不确认
#   --verify-only          仅验证备份，不恢复
#   --rollback             回滚到最近的恢复点
#   --help                 显示此帮助信息
#
# 环境变量：
#   AQSP_PROJECT_ROOT      项目根目录
#   AQSP_BACKUP_DIR        备份目录
#   AQSP_RESTORE_LOG_DIR   日志目录

set -euo pipefail

# ============================================================================
# 配置和初始化
# ============================================================================

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_DIR="${AQSP_BACKUP_DIR:-${PROJECT_ROOT}/backups}"
RESTORE_POINT_DIR="${BACKUP_DIR}/.restore_points"
LOG_DIR="${AQSP_RESTORE_LOG_DIR:-${PROJECT_ROOT}/logs/restore}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

# 操作模式
LIST_MODE=false
VERIFY_ONLY=false
ROLLBACK_MODE=false
RESTORE_LATEST=false
FORCE_RESTORE=false
SKIP_RESTORE_POINT=false

# 恢复配置
BACKUP_FILE=""
RESTORE_POINT_NAME=""

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

confirm() {
    local prompt="$1"
    if [ "$FORCE_RESTORE" = true ]; then
        log "[FORCE] 跳过确认: $prompt"
        return 0
    fi

    echo -n "$prompt [y/N] "
    read -r response
    case "$response" in
        [yY][eE][sS]|[yY])
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# ============================================================================
# 参数解析
# ============================================================================

while [[ $# -gt 0 ]]; do
    case "$1" in
        --list|-l)
            LIST_MODE=true
            shift
            ;;
        --backup|-b)
            BACKUP_FILE="$2"
            shift 2
            ;;
        --latest)
            RESTORE_LATEST=true
            shift
            ;;
        --backup-dir)
            BACKUP_DIR="$2"
            shift 2
            ;;
        --restore-point)
            RESTORE_POINT_NAME="$2"
            shift 2
            ;;
        --skip-restore-point)
            SKIP_RESTORE_POINT=true
            shift
            ;;
        --force|-f)
            FORCE_RESTORE=true
            shift
            ;;
        --verify-only)
            VERIFY_ONLY=true
            shift
            ;;
        --rollback)
            ROLLBACK_MODE=true
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
# 准备环境
# ============================================================================

mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/restore-${TIMESTAMP}.log"

# ============================================================================
# 列出可用备份
# ============================================================================

list_backups() {
    log "=== 可用备份列表 ==="
    echo ""

    if [ ! -d "$BACKUP_DIR" ]; then
        log "备份目录不存在: $BACKUP_DIR"
        exit 1
    fi

    local backup_files=()
    while IFS= read -r file; do
        backup_files+=("$file")
    done < <(find "$BACKUP_DIR" -maxdepth 1 -name "backup-*.tar.gz" -type f | sort -r)

    if [ ${#backup_files[@]} -eq 0 ]; then
        log "未找到任何备份文件"
        exit 0
    fi

    printf "%-3s %-20s %-12s %-20s\n" "No" "备份名称" "大小" "创建时间"
    printf "%s\n" "$(printf '=%.0s' {1..80})"

    local idx=1
    for backup in "${backup_files[@]}"; do
        local basename=$(basename "$backup")
        local size=$(stat -f %z "$backup" 2>/dev/null || stat -c %s "$backup")
        local mtime=$(stat -f %Sm -t "%Y-%m-%d %H:%M:%S" "$backup" 2>/dev/null || stat -c %y "$backup" | cut -d. -f1)

        printf "%-3d %-20s %-12s %-20s\n" \
            "$idx" \
            "${basename%.tar.gz}" \
            "$(human_size $size)" \
            "$mtime"

        idx=$((idx + 1))
    done

    echo ""
    log "共找到 ${#backup_files[@]} 个备份"

    # 列出恢复点
    if [ -d "$RESTORE_POINT_DIR" ]; then
        local restore_points=()
        while IFS= read -r dir; do
            restore_points+=("$dir")
        done < <(find "$RESTORE_POINT_DIR" -maxdepth 1 -name "restore_point-*" -type d | sort -r)

        if [ ${#restore_points[@]} -gt 0 ]; then
            echo ""
            log "=== 可用恢复点 ==="
            printf "%-3s %-30s %-20s\n" "No" "恢复点名称" "创建时间"
            printf "%s\n" "$(printf '=%.0s' {1..80})"

            local rp_idx=1
            for rp in "${restore_points[@]}"; do
                local rp_basename=$(basename "$rp")
                local rp_mtime=$(stat -f %Sm -t "%Y-%m-%d %H:%M:%S" "$rp" 2>/dev/null || stat -c %y "$rp" | cut -d. -f1)

                printf "%-3d %-30s %-20s\n" \
                    "$rp_idx" \
                    "$rp_basename" \
                    "$rp_mtime"

                rp_idx=$((rp_idx + 1))
            done

            echo ""
            log "共找到 ${#restore_points[@]} 个恢复点"
        fi
    fi
}

if [ "$LIST_MODE" = true ]; then
    list_backups
    exit 0
fi

# ============================================================================
# 回滚模式
# ============================================================================

rollback_to_restore_point() {
    log "=== 回滚到恢复点 @ $(date) ==="

    if [ ! -d "$RESTORE_POINT_DIR" ]; then
        log_error "恢复点目录不存在: $RESTORE_POINT_DIR"
        exit 1
    fi

    # 找到最新的恢复点
    local latest_restore_point=$(find "$RESTORE_POINT_DIR" -maxdepth 1 -name "restore_point-*" -type d | sort -r | head -1)

    if [ -z "$latest_restore_point" ]; then
        log_error "未找到任何恢复点"
        exit 1
    fi

    log "最新恢复点: $(basename "$latest_restore_point")"

    if ! confirm "确认回滚到此恢复点？"; then
        log "用户取消回滚操作"
        exit 0
    fi

    log "开始回滚..."

    # 恢复数据
    cd "$PROJECT_ROOT"
    rsync -av --delete "${latest_restore_point}/" . 2>&1 | tee -a "$LOG_FILE"

    log "回滚完成"
    log "日志: $LOG_FILE"
    exit 0
}

if [ "$ROLLBACK_MODE" = true ]; then
    rollback_to_restore_point
fi

# ============================================================================
# 确定要恢复的备份文件
# ============================================================================

if [ "$RESTORE_LATEST" = true ]; then
    BACKUP_FILE=$(find "$BACKUP_DIR" -maxdepth 1 -name "backup-*.tar.gz" -type f | sort -r | head -1)
    if [ -z "$BACKUP_FILE" ]; then
        log_error "未找到任何备份文件"
        exit 1
    fi
    log "选择最新备份: $(basename "$BACKUP_FILE")"
elif [ -z "$BACKUP_FILE" ]; then
    log_error "请使用 --backup 指定备份文件，或使用 --latest 恢复最新备份"
    echo "使用 --list 查看可用备份" >&2
    exit 1
fi

# 验证备份文件存在
if [ ! -f "$BACKUP_FILE" ]; then
    # 尝试在备份目录中查找
    if [ -f "${BACKUP_DIR}/${BACKUP_FILE}" ]; then
        BACKUP_FILE="${BACKUP_DIR}/${BACKUP_FILE}"
    elif [ -f "${BACKUP_DIR}/${BACKUP_FILE}.tar.gz" ]; then
        BACKUP_FILE="${BACKUP_DIR}/${BACKUP_FILE}.tar.gz"
    else
        log_error "备份文件不存在: $BACKUP_FILE"
        exit 1
    fi
fi

# ============================================================================
# 验证备份完整性
# ============================================================================

log "=== 验证备份完整性 @ $(date) ==="
log "备份文件: $BACKUP_FILE"

backup_size=$(stat -f %z "$BACKUP_FILE" 2>/dev/null || stat -c %s "$BACKUP_FILE")
log "备份大小: $(human_size $backup_size)"

log "验证 tar 压缩包完整性..."
if ! tar -tzf "$BACKUP_FILE" >/dev/null 2>&1; then
    log_error "备份文件损坏或不是有效的 tar.gz 文件"
    exit 2
fi
log "✓ 压缩包完整性验证通过"

# 提取并验证元数据
log "提取备份元数据..."
TEMP_EXTRACT_DIR=$(mktemp -d)
trap "rm -rf '$TEMP_EXTRACT_DIR'" EXIT

backup_name=$(tar -tzf "$BACKUP_FILE" | head -1 | cut -d/ -f1)
metadata_file="${TEMP_EXTRACT_DIR}/metadata.env"

if tar -xzf "$BACKUP_FILE" -C "$TEMP_EXTRACT_DIR" "${backup_name}/metadata.env" 2>/dev/null; then
    log "元数据:"
    cat "${TEMP_EXTRACT_DIR}/metadata.env" | tee -a "$LOG_FILE"

    # 加载元数据
    # shellcheck disable=SC1090
    source "${TEMP_EXTRACT_DIR}/metadata.env"

    log "备份包含 $FILE_COUNT 个文件，总大小: $(human_size $TOTAL_SIZE)"
else
    log "警告: 无法提取元数据文件"
fi

# 提取并验证清单
manifest_file="${TEMP_EXTRACT_DIR}/manifest.txt"
if tar -xzf "$BACKUP_FILE" -C "$TEMP_EXTRACT_DIR" "${backup_name}/manifest.txt" 2>/dev/null; then
    log "备份清单:"
    cat "${TEMP_EXTRACT_DIR}/manifest.txt" | tee -a "$LOG_FILE"
else
    log "警告: 无法提取清单文件"
fi

if [ "$VERIFY_ONLY" = true ]; then
    log "=== 验证完成（仅验证模式） ==="
    exit 0
fi

# ============================================================================
# 创建恢复点
# ============================================================================

if [ "$SKIP_RESTORE_POINT" = false ]; then
    if [ -z "$RESTORE_POINT_NAME" ]; then
        RESTORE_POINT_NAME="restore_point-${TIMESTAMP}"
    fi

    RESTORE_POINT_PATH="${RESTORE_POINT_DIR}/${RESTORE_POINT_NAME}"

    log "=== 创建恢复点 @ $(date) ==="
    log "恢复点: $RESTORE_POINT_PATH"

    mkdir -p "$RESTORE_POINT_DIR"

    # 备份当前状态
    log "备份当前数据状态..."
    backup_items=(
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

    mkdir -p "$RESTORE_POINT_PATH"
    for item in "${backup_items[@]}"; do
        item_path="${PROJECT_ROOT}/${item}"
        if [ -e "$item_path" ]; then
            parent_dir=$(dirname "${RESTORE_POINT_PATH}/${item}")
            mkdir -p "$parent_dir"
            cp -a "$item_path" "${RESTORE_POINT_PATH}/${item}"
            log "已保存: $item"
        fi
    done

    log "✓ 恢复点创建完成"
fi

# ============================================================================
# 执行恢复
# ============================================================================

log "=== 开始恢复 @ $(date) ==="

if ! confirm "确认从备份恢复数据？此操作将覆盖现有数据！"; then
    log "用户取消恢复操作"
    exit 0
fi

log "解压备份到临时目录..."
extract_dir=$(mktemp -d)
trap "rm -rf '$extract_dir'" EXIT

tar -xzf "$BACKUP_FILE" -C "$extract_dir" 2>&1 | tee -a "$LOG_FILE"

log "恢复数据文件..."
cd "$extract_dir/$backup_name"

# 恢复每个项目
for item in data config reports; do
    if [ -e "$item" ]; then
        target="${PROJECT_ROOT}/${item}"
        log "恢复: $item -> $target"

        # 创建父目录
        mkdir -p "$(dirname "$target")"

        # 复制文件/目录
        if [ -d "$item" ]; then
            rsync -av "$item/" "$target/" 2>&1 | tee -a "$LOG_FILE"
        else
            cp -a "$item" "$target"
        fi

        log "✓ 已恢复: $item"
    fi
done

# ============================================================================
# 验证恢复结果
# ============================================================================

log "验证恢复结果..."
verification_passed=true

for item in data/predictions.jsonl data/paper_trades.jsonl config; do
    item_path="${PROJECT_ROOT}/${item}"
    if [ ! -e "$item_path" ]; then
        log_error "验证失败: $item 不存在"
        verification_passed=false
    fi
done

if [ "$verification_passed" = true ]; then
    log "✓ 恢复验证通过"
else
    log_error "✗ 恢复验证失败"
    if [ "$SKIP_RESTORE_POINT" = false ]; then
        log "可以使用 --rollback 回滚到恢复点"
    fi
    exit 3
fi

# ============================================================================
# 清理旧恢复点
# ============================================================================

if [ -d "$RESTORE_POINT_DIR" ]; then
    log "清理旧恢复点（保留最近 5 个）..."
    restore_point_count=$(find "$RESTORE_POINT_DIR" -maxdepth 1 -name "restore_point-*" -type d | wc -l | tr -d ' ')

    if [ "$restore_point_count" -gt 5 ]; then
        find "$RESTORE_POINT_DIR" -maxdepth 1 -name "restore_point-*" -type d -print0 | \
            xargs -0 ls -td | \
            tail -n "+6" | \
            while IFS= read -r old_rp; do
                log "删除旧恢复点: $(basename "$old_rp")"
                rm -rf "$old_rp"
            done
    fi
fi

# ============================================================================
# 恢复完成报告
# ============================================================================

log "=== 恢复完成 @ $(date) ==="
log "已从备份恢复: $(basename "$BACKUP_FILE")"
if [ "$SKIP_RESTORE_POINT" = false ]; then
    log "恢复点: $RESTORE_POINT_NAME"
    log "如需回滚，使用: $0 --rollback"
fi
log "日志文件: $LOG_FILE"

exit 0
