#!/usr/bin/env bash
# 创建向后兼容符号链接
# 用途：保持旧路径可用，避免破坏现有配置
# 执行：bash scripts/create_symlinks.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔗 创建符号链接以保持向后兼容..."

# 高频使用脚本的符号链接
declare -A SYMLINKS=(
    # 维护脚本
    ["bt_task.sh"]="maintenance/bt_task.sh"
    ["daily_pipeline.sh"]="maintenance/daily_pipeline.sh"
    ["daily_pipeline.py"]="maintenance/daily_pipeline.py"
    ["daily_run.sh"]="maintenance/daily_run.sh"
    ["coldstart_daily.sh"]="maintenance/coldstart_daily.sh"
    ["intraday_refresh.sh"]="maintenance/intraday_refresh.sh"
    ["midday_refresh.sh"]="maintenance/midday_refresh.sh"
    ["variant_refresh.sh"]="maintenance/variant_refresh.sh"
    ["news_catalysts.sh"]="maintenance/news_catalysts.sh"
    ["runtime_python.sh"]="maintenance/runtime_python.sh"
    ["clear_locks.sh"]="maintenance/clear_locks.sh"
    ["setup_log_rotation.sh"]="maintenance/setup_log_rotation.sh"

    # 监控脚本
    ["server_monitor.sh"]="monitor/server_monitor.sh"
    ["server_status.sh"]="monitor/server_status.sh"
    ["health_vibe_research.sh"]="monitor/health_vibe_research.sh"
    ["check_vibe_research.sh"]="monitor/check_vibe_research.sh"
    ["check_scheduler.py"]="monitor/check_scheduler.py"
    ["safe_probe.sh"]="monitor/safe_probe.sh"
    ["server_doctor.py"]="monitor/server_doctor.py"
    ["analyze_logs.py"]="monitor/analyze_logs.py"

    # 部署脚本
    ["deploy_dashboard.sh"]="deploy/deploy_dashboard.sh"
    ["deploy_immutable_release.sh"]="deploy/deploy_immutable_release.sh"
    ["install_server_cron.sh"]="deploy/install_server_cron.sh"
    ["install_vibe_research_systemd.sh"]="deploy/install_vibe_research_systemd.sh"

    # 备份脚本
    ["backup.sh"]="backup/backup.sh"
    ["backup_verify.sh"]="backup/backup_verify.sh"
    ["restore.sh"]="backup/restore.sh"

    # 开发脚本
    ["dev.sh"]="dev/dev.sh"

    # 生产脚本
    ["start_dashboard.sh"]="production/start_dashboard.sh"
    ["start_vibe_research.sh"]="production/start_vibe_research.sh"
    ["start_vibe_research_service.sh"]="production/start_vibe_research_service.sh"
    ["stop_vibe_research_service.sh"]="production/stop_vibe_research_service.sh"
)

created=0
skipped=0
failed=0

for link_name in "${!SYMLINKS[@]}"; do
    target="${SYMLINKS[$link_name]}"

    # 检查目标文件是否存在
    if [ ! -f "$target" ]; then
        echo "⚠️  跳过 $link_name → $target (目标不存在)"
        ((failed++))
        continue
    fi

    # 如果符号链接已存在且正确，跳过
    if [ -L "$link_name" ] && [ "$(readlink "$link_name")" = "$target" ]; then
        echo "✓  已存在 $link_name → $target"
        ((skipped++))
        continue
    fi

    # 如果存在同名文件（非符号链接），警告并跳过
    if [ -e "$link_name" ] && [ ! -L "$link_name" ]; then
        echo "⚠️  跳过 $link_name (已存在同名文件，非符号链接)"
        ((failed++))
        continue
    fi

    # 删除旧符号链接（如果存在）
    [ -L "$link_name" ] && rm "$link_name"

    # 创建符号链接
    if ln -s "$target" "$link_name" 2>/dev/null; then
        echo "✓  创建 $link_name → $target"
        ((created++))
    else
        echo "✗  失败 $link_name → $target"
        ((failed++))
    fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 符号链接创建完成"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  新建: $created"
echo "  跳过: $skipped"
echo "  失败: $failed"
echo "  总计: $((created + skipped + failed))"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ $failed -gt 0 ]; then
    echo ""
    echo "⚠️  部分符号链接创建失败，请手动检查"
    exit 1
fi

echo ""
echo "✅ 所有符号链接已就绪！"
echo ""
echo "验证命令："
echo "  ls -l scripts/*.sh 2>/dev/null | grep '->'"
echo "  scripts/bt_task.sh status"
echo "  scripts/server_status.sh"
