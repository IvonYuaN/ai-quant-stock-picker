#!/usr/bin/env bash
# 备份验证脚本 - 验证备份完整性并生成报告
#
# 功能：
# - 验证备份文件完整性
# - 检查文件数量和大小
# - 对比元数据与实际内容
# - 生成详细的验证报告
# - 支持批量验证
#
# 用法：
#   backup_verify.sh [OPTIONS]
#
# 选项：
#   --backup FILE          验证指定备份文件
#   --all                  验证所有备份文件
#   --backup-dir DIR       备份目录（默认: $PROJECT_ROOT/backups）
#   --report FILE          报告输出文件（默认: 控制台）
#   --quiet                静默模式，仅输出错误
#   --help                 显示此帮助信息
#
# 环境变量：
#   AQSP_PROJECT_ROOT      项目根目录
#   AQSP_BACKUP_DIR        备份目录
#   AQSP_VERIFY_LOG_DIR    日志目录

set -euo pipefail

# ============================================================================
# 配置和初始化
# ============================================================================

PROJECT_ROOT="${AQSP_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_DIR="${AQSP_BACKUP_DIR:-${PROJECT_ROOT}/backups}"
LOG_DIR="${AQSP_VERIFY_LOG_DIR:-${PROJECT_ROOT}/logs/verify}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

# 操作模式
VERIFY_ALL=false
QUIET_MODE=false
BACKUP_FILE=""
REPORT_FILE=""

# 统计信息
TOTAL_VERIFIED=0
TOTAL_PASSED=0
TOTAL_FAILED=0

# ============================================================================
# 工具函数
# ============================================================================

log() {
    if [ "$QUIET_MODE" = false ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
    fi
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $1" >&2 | tee -a "$LOG_FILE"
}

log_report() {
    local msg="$1"
    if [ -n "$REPORT_FILE" ]; then
        echo "$msg" >> "$REPORT_FILE"
    fi
    if [ "$QUIET_MODE" = false ]; then
        echo "$msg"
    fi
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
        --backup|-b)
            BACKUP_FILE="$2"
            shift 2
            ;;
        --all|-a)
            VERIFY_ALL=true
            shift
            ;;
        --backup-dir)
            BACKUP_DIR="$2"
            shift 2
            ;;
        --report|-r)
            REPORT_FILE="$2"
            shift 2
            ;;
        --quiet|-q)
            QUIET_MODE=true
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
LOG_FILE="${LOG_DIR}/verify-${TIMESTAMP}.log"

if [ -n "$REPORT_FILE" ]; then
    mkdir -p "$(dirname "$REPORT_FILE")"
    : > "$REPORT_FILE"  # 清空报告文件
fi

# ============================================================================
# 验证单个备份
# ============================================================================

verify_backup() {
    local backup_path="$1"
    local backup_name=$(basename "$backup_path")

    log_report ""
    log_report "=========================================="
    log_report "验证备份: $backup_name"
    log_report "=========================================="

    TOTAL_VERIFIED=$((TOTAL_VERIFIED + 1))

    # 检查文件是否存在
    if [ ! -f "$backup_path" ]; then
        log_error "备份文件不存在: $backup_path"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        return 1
    fi

    # 获取文件信息
    local file_size=$(stat -f %z "$backup_path" 2>/dev/null || stat -c %s "$backup_path")
    local file_mtime=$(stat -f %Sm -t "%Y-%m-%d %H:%M:%S" "$backup_path" 2>/dev/null || stat -c %y "$backup_path" | cut -d. -f1)

    log_report "文件大小: $(human_size $file_size)"
    log_report "修改时间: $file_mtime"

    # 验证 tar 压缩包完整性
    log_report ""
    log_report "1. 验证压缩包完整性..."

    if ! tar -tzf "$backup_path" >/dev/null 2>&1; then
        log_error "✗ 压缩包损坏或不是有效的 tar.gz 文件"
        log_report "状态: FAILED - 压缩包损坏"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        return 1
    fi
    log_report "✓ 压缩包完整性验证通过"

    # 提取文件列表
    log_report ""
    log_report "2. 分析备份内容..."

    local temp_dir=$(mktemp -d)
    trap "rm -rf '$temp_dir'" RETURN

    local file_list="${temp_dir}/files.txt"
    tar -tzf "$backup_path" > "$file_list"

    local total_files=$(wc -l < "$file_list" | tr -d ' ')
    log_report "包含文件数: $total_files"

    # 提取备份根目录名
    local backup_root=$(head -1 "$file_list" | cut -d/ -f1)

    # 验证必需文件
    log_report ""
    log_report "3. 验证必需文件..."

    local required_files=(
        "${backup_root}/metadata.env"
        "${backup_root}/manifest.txt"
    )

    local missing_files=()
    for req_file in "${required_files[@]}"; do
        if ! grep -q "^${req_file}$" "$file_list"; then
            missing_files+=("$req_file")
        fi
    done

    if [ ${#missing_files[@]} -gt 0 ]; then
        log_report "✗ 缺少必需文件:"
        for missing in "${missing_files[@]}"; do
            log_report "  - $missing"
        done
    else
        log_report "✓ 所有必需文件存在"
    fi

    # 提取并验证元数据
    log_report ""
    log_report "4. 验证元数据..."

    if tar -xzf "$backup_path" -C "$temp_dir" "${backup_root}/metadata.env" 2>/dev/null; then
        local metadata_file="${temp_dir}/${backup_root}/metadata.env"

        # 加载元数据
        local BACKUP_NAME=""
        local BACKUP_TIMESTAMP=""
        local BACKUP_DATE=""
        local PROJECT_ROOT_BACKUP=""
        local FILE_COUNT=""
        local TOTAL_SIZE=""
        local HOSTNAME=""
        local USER=""

        # shellcheck disable=SC1090
        source "$metadata_file"

        log_report "元数据内容:"
        log_report "  备份名称: $BACKUP_NAME"
        log_report "  备份时间: $BACKUP_DATE"
        log_report "  文件数量: $FILE_COUNT"
        log_report "  总大小: $(human_size ${TOTAL_SIZE:-0})"
        log_report "  主机: $HOSTNAME"
        log_report "  用户: $USER"

        # 验证文件数量
        if [ -n "$FILE_COUNT" ]; then
            local actual_data_files=$(grep -c "^${backup_root}/\(data\|config\|reports\)/" "$file_list" || echo 0)
            if [ "$actual_data_files" -ge "$FILE_COUNT" ]; then
                log_report "✓ 文件数量验证通过 (声明: $FILE_COUNT, 实际: $actual_data_files)"
            else
                log_report "✗ 文件数量不匹配 (声明: $FILE_COUNT, 实际: $actual_data_files)"
            fi
        fi
    else
        log_report "✗ 无法提取元数据文件"
    fi

    # 提取并验证清单
    log_report ""
    log_report "5. 验证备份清单..."

    if tar -xzf "$backup_path" -C "$temp_dir" "${backup_root}/manifest.txt" 2>/dev/null; then
        local manifest_file="${temp_dir}/${backup_root}/manifest.txt"
        local manifest_entries=$(wc -l < "$manifest_file" | tr -d ' ')

        log_report "清单条目数: $manifest_entries"
        log_report ""
        log_report "备份项目:"

        while IFS='|' read -r item type size count; do
            if [ "$type" = "dir" ]; then
                log_report "  [$type] $item - $(human_size $size) ($count 个文件)"
            else
                log_report "  [$type] $item - $(human_size $size)"
            fi
        done < "$manifest_file"
    else
        log_report "✗ 无法提取清单文件"
    fi

    # 验证关键数据文件
    log_report ""
    log_report "6. 验证关键数据文件..."

    local critical_files=(
        "${backup_root}/data/predictions.jsonl"
        "${backup_root}/data/paper_trades.jsonl"
        "${backup_root}/config"
    )

    local critical_missing=()
    for critical in "${critical_files[@]}"; do
        if ! grep -q "^${critical}" "$file_list"; then
            critical_missing+=("$critical")
        else
            log_report "✓ 找到: $critical"
        fi
    done

    if [ ${#critical_missing[@]} -gt 0 ]; then
        log_report "⚠ 警告: 缺少部分关键文件:"
        for missing in "${critical_missing[@]}"; do
            log_report "  - $missing"
        done
    fi

    # 测试解压
    log_report ""
    log_report "7. 测试解压..."

    local extract_test_dir="${temp_dir}/extract_test"
    mkdir -p "$extract_test_dir"

    if tar -xzf "$backup_path" -C "$extract_test_dir" 2>&1 | head -20 | tee -a "$LOG_FILE" | grep -q "Error"; then
        log_report "✗ 解压测试失败"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        return 1
    else
        log_report "✓ 解压测试通过"
    fi

    # 最终判定
    log_report ""
    if [ ${#missing_files[@]} -eq 0 ] && [ ${#critical_missing[@]} -eq 0 ]; then
        log_report "状态: PASSED ✓"
        log_report "备份 $backup_name 验证通过"
        TOTAL_PASSED=$((TOTAL_PASSED + 1))
        return 0
    else
        log_report "状态: WARNING ⚠"
        log_report "备份 $backup_name 存在警告，但基本可用"
        TOTAL_PASSED=$((TOTAL_PASSED + 1))
        return 0
    fi
}

# ============================================================================
# 主程序逻辑
# ============================================================================

log "=== 备份验证 @ $(date) ==="
log "备份目录: $BACKUP_DIR"

if [ -n "$REPORT_FILE" ]; then
    log "报告文件: $REPORT_FILE"
    log_report "# 备份验证报告"
    log_report "生成时间: $(date '+%Y-%m-%d %H:%M:%S')"
    log_report "备份目录: $BACKUP_DIR"
    log_report ""
fi

# 确定要验证的备份列表
backup_list=()

if [ "$VERIFY_ALL" = true ]; then
    log "验证所有备份文件..."

    if [ ! -d "$BACKUP_DIR" ]; then
        log_error "备份目录不存在: $BACKUP_DIR"
        exit 1
    fi

    while IFS= read -r file; do
        backup_list+=("$file")
    done < <(find "$BACKUP_DIR" -maxdepth 1 -name "backup-*.tar.gz" -type f | sort)

    if [ ${#backup_list[@]} -eq 0 ]; then
        log "未找到任何备份文件"
        exit 0
    fi

    log "找到 ${#backup_list[@]} 个备份文件"

elif [ -n "$BACKUP_FILE" ]; then
    # 验证单个备份
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

    backup_list=("$BACKUP_FILE")
else
    log_error "请使用 --backup 指定备份文件，或使用 --all 验证所有备份"
    echo "使用 --help 查看帮助信息" >&2
    exit 1
fi

# 执行验证
for backup in "${backup_list[@]}"; do
    verify_backup "$backup" || true
done

# ============================================================================
# 生成汇总报告
# ============================================================================

log_report ""
log_report "=========================================="
log_report "验证汇总"
log_report "=========================================="
log_report "总验证数: $TOTAL_VERIFIED"
log_report "通过数量: $TOTAL_PASSED"
log_report "失败数量: $TOTAL_FAILED"

if [ "$TOTAL_FAILED" -eq 0 ]; then
    log_report ""
    log_report "✓ 所有备份验证通过"
    exit_code=0
else
    log_report ""
    log_report "✗ 有 $TOTAL_FAILED 个备份验证失败"
    exit_code=1
fi

log_report ""
log_report "验证完成时间: $(date '+%Y-%m-%d %H:%M:%S')"
log_report "详细日志: $LOG_FILE"

log "=== 验证完成 ==="

exit $exit_code
