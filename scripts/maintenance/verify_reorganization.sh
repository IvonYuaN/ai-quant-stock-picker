#!/usr/bin/env bash
# 验证脚本目录重组
# 用途：检查所有脚本是否正确分类，引用是否有效
# 执行：bash scripts/verify_reorganization.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 验证脚本目录重组"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ============================================================================
# 1. 检查目录结构
# ============================================================================
echo -e "${BLUE}📁 1. 检查目录结构${NC}"
echo ""

expected_dirs=(
    "dev"
    "deploy"
    "monitor"
    "backup"
    "data"
    "maintenance"
    "production"
    "analysis"
    "runner"
    "debate"
    "launchd"
    "legacy"
)

missing_dirs=0
for dir in "${expected_dirs[@]}"; do
    if [ -d "$dir" ]; then
        echo -e "${GREEN}✓${NC} $dir/ 存在"
    else
        echo -e "${RED}✗${NC} $dir/ 缺失"
        ((missing_dirs++))
    fi
done

if [ $missing_dirs -eq 0 ]; then
    echo -e "${GREEN}✓ 所有预期目录存在${NC}"
else
    echo -e "${RED}✗ $missing_dirs 个目录缺失${NC}"
fi
echo ""

# ============================================================================
# 2. 统计脚本数量
# ============================================================================
echo -e "${BLUE}📊 2. 统计脚本数量${NC}"
echo ""

printf "%-15s %10s %10s %10s\n" "目录" "Shell" "Python" "总计"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

total_sh=0
total_py=0

for dir in "${expected_dirs[@]}"; do
    if [ ! -d "$dir" ]; then
        continue
    fi

    sh_count=$(find "$dir" -maxdepth 1 -type f -name "*.sh" 2>/dev/null | wc -l | tr -d ' ')
    py_count=$(find "$dir" -maxdepth 1 -type f -name "*.py" 2>/dev/null | wc -l | tr -d ' ')
    total=$((sh_count + py_count))

    if [ $total -gt 0 ]; then
        printf "%-15s %10s %10s %10s\n" "$dir/" "$sh_count" "$py_count" "$total"
        total_sh=$((total_sh + sh_count))
        total_py=$((total_py + py_count))
    fi
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "%-15s %10s %10s %10s\n" "总计" "$total_sh" "$total_py" "$((total_sh + total_py))"
echo ""

# ============================================================================
# 3. 检查根目录遗留脚本
# ============================================================================
echo -e "${BLUE}🔍 3. 检查根目录遗留脚本${NC}"
echo ""

orphaned_scripts=$(find . -maxdepth 1 -type f \( -name "*.sh" -o -name "*.py" \) 2>/dev/null | wc -l | tr -d ' ')

if [ "$orphaned_scripts" -eq 0 ]; then
    echo -e "${GREEN}✓ 根目录无遗留脚本${NC}"
else
    echo -e "${YELLOW}⚠ 根目录发现 $orphaned_scripts 个脚本文件：${NC}"
    find . -maxdepth 1 -type f \( -name "*.sh" -o -name "*.py" \) 2>/dev/null | sed 's|^\./|  - |'
    echo ""
    echo -e "${YELLOW}建议：将这些脚本移动到合适的子目录${NC}"
fi
echo ""

# ============================================================================
# 4. 检查符号链接
# ============================================================================
echo -e "${BLUE}🔗 4. 检查符号链接${NC}"
echo ""

symlinks=$(find . -maxdepth 1 -type l 2>/dev/null | wc -l | tr -d ' ')

if [ "$symlinks" -eq 0 ]; then
    echo -e "${YELLOW}⚠ 未发现符号链接${NC}"
    echo "  建议运行: bash scripts/create_symlinks.sh"
else
    echo -e "${GREEN}✓ 发现 $symlinks 个符号链接${NC}"
    echo ""

    broken=0
    valid=0

    while IFS= read -r link; do
        link_name=$(basename "$link")
        target=$(readlink "$link")

        if [ -e "$link" ]; then
            echo -e "${GREEN}✓${NC} $link_name → $target"
            ((valid++))
        else
            echo -e "${RED}✗${NC} $link_name → $target (损坏)"
            ((broken++))
        fi
    done < <(find . -maxdepth 1 -type l 2>/dev/null)

    echo ""
    if [ $broken -eq 0 ]; then
        echo -e "${GREEN}✓ 所有符号链接有效${NC}"
    else
        echo -e "${RED}✗ $broken 个符号链接损坏${NC}"
    fi
fi
echo ""

# ============================================================================
# 5. 检查关键脚本可执行权限
# ============================================================================
echo -e "${BLUE}🔐 5. 检查关键脚本可执行权限${NC}"
echo ""

critical_scripts=(
    "maintenance/bt_task.sh"
    "maintenance/daily_pipeline.sh"
    "monitor/server_monitor.sh"
    "monitor/server_status.sh"
    "monitor/health_vibe_research.sh"
    "backup/backup.sh"
    "backup/restore.sh"
    "dev/dev.sh"
)

non_executable=0

for script in "${critical_scripts[@]}"; do
    if [ -f "$script" ]; then
        if [ -x "$script" ]; then
            echo -e "${GREEN}✓${NC} $script"
        else
            echo -e "${RED}✗${NC} $script (不可执行)"
            ((non_executable++))
        fi
    else
        echo -e "${YELLOW}⚠${NC} $script (不存在)"
    fi
done

echo ""
if [ $non_executable -eq 0 ]; then
    echo -e "${GREEN}✓ 所有关键脚本可执行${NC}"
else
    echo -e "${RED}✗ $non_executable 个脚本不可执行${NC}"
    echo "  修复命令: chmod +x scripts/path/to/script.sh"
fi
echo ""

# ============================================================================
# 6. 检查脚本内部引用 runtime_python.sh
# ============================================================================
echo -e "${BLUE}🔗 6. 检查 runtime_python.sh 引用${NC}"
echo ""

old_pattern='scripts/runtime_python\.sh'
correct_pattern='scripts/maintenance/runtime_python\.sh'

files_with_old_ref=0
total_checked=0

while IFS= read -r file; do
    ((total_checked++))
    if grep -q "$old_pattern" "$file" 2>/dev/null; then
        if ! grep -q "$correct_pattern" "$file" 2>/dev/null; then
            echo -e "${YELLOW}⚠${NC} $file (使用旧路径)"
            ((files_with_old_ref++))
        fi
    fi
done < <(find . -type f \( -name "*.sh" -o -name "*.py" \) -not -path "./__pycache__/*" 2>/dev/null)

echo "检查了 $total_checked 个脚本文件"
echo ""

if [ $files_with_old_ref -eq 0 ]; then
    echo -e "${GREEN}✓ 所有引用已更新或使用符号链接${NC}"
else
    echo -e "${YELLOW}⚠ $files_with_old_ref 个文件使用旧路径${NC}"
    echo "  如已创建符号链接，这不是问题"
    echo "  否则需要更新路径: scripts/runtime_python.sh → scripts/maintenance/runtime_python.sh"
fi
echo ""

# ============================================================================
# 7. 快速功能测试
# ============================================================================
echo -e "${BLUE}🧪 7. 快速功能测试${NC}"
echo ""

# 测试 bt_task.sh
if [ -f "maintenance/bt_task.sh" ] || [ -L "bt_task.sh" ]; then
    script_to_test="bt_task.sh"
    [ ! -e "$script_to_test" ] && script_to_test="maintenance/bt_task.sh"

    if bash "$script_to_test" status &>/dev/null; then
        echo -e "${GREEN}✓${NC} bt_task.sh status 执行成功"
    else
        echo -e "${YELLOW}⚠${NC} bt_task.sh status 执行失败（可能需要环境配置）"
    fi
else
    echo -e "${RED}✗${NC} bt_task.sh 不存在"
fi

# 测试 server_status.sh
if [ -f "monitor/server_status.sh" ] || [ -L "server_status.sh" ]; then
    script_to_test="server_status.sh"
    [ ! -e "$script_to_test" ] && script_to_test="monitor/server_status.sh"

    if bash -n "$script_to_test" &>/dev/null; then
        echo -e "${GREEN}✓${NC} server_status.sh 语法检查通过"
    else
        echo -e "${RED}✗${NC} server_status.sh 语法错误"
    fi
else
    echo -e "${YELLOW}⚠${NC} server_status.sh 不存在"
fi

echo ""

# ============================================================================
# 总结
# ============================================================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${BLUE}📋 验证总结${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

issues=0
[ $missing_dirs -gt 0 ] && ((issues++))
[ "$orphaned_scripts" -gt 0 ] && ((issues++))
[ "$symlinks" -eq 0 ] && ((issues++))
[ $broken -gt 0 ] && ((issues++))
[ $non_executable -gt 0 ] && ((issues++))

if [ $issues -eq 0 ]; then
    echo -e "${GREEN}✓ 脚本目录重组验证通过！${NC}"
    echo ""
    echo "后续步骤："
    echo "  1. 如未创建符号链接，运行: bash scripts/create_symlinks.sh"
    echo "  2. 更新外部引用（cron、systemd）"
    echo "  3. 参考 scripts/MIGRATION_GUIDE.md"
else
    echo -e "${YELLOW}⚠ 发现 $issues 类问题，请检查上述输出${NC}"
    echo ""
    echo "建议操作："
    echo "  1. 将根目录遗留脚本移动到合适目录"
    echo "  2. 运行: bash scripts/create_symlinks.sh"
    echo "  3. 修复损坏的符号链接"
    echo "  4. 添加可执行权限: chmod +x scripts/**/*.sh"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
