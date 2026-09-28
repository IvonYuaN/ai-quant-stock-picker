# 脚本目录重组迁移指南

> **重组日期**: 2026-09-28  
> **影响范围**: 所有引用 scripts/ 目录的配置和脚本  
> **向后兼容**: 建议创建符号链接保持兼容性

---

## 📋 变更摘要

scripts/ 目录已按功能重新组织，所有脚本移动到对应的子目录：

- `dev/` - 开发工具
- `deploy/` - 部署脚本
- `monitor/` - 监控检查
- `backup/` - 备份恢复
- `data/` - 数据处理
- `maintenance/` - 日常维护
- `production/` - 生产运行
- `analysis/` - 分析报告
- `runner/` - Walk-forward gate
- `debate/` - 辩论系统
- `launchd/` - macOS 调度（保持不变）

---

## 🔄 路径映射表

### 高频使用脚本

| 旧路径 | 新路径 | 类型 |
|--------|--------|------|
| `scripts/bt_task.sh` | `scripts/maintenance/bt_task.sh` | 宝塔任务入口 |
| `scripts/daily_pipeline.sh` | `scripts/maintenance/daily_pipeline.sh` | 每日跑批 |
| `scripts/daily_pipeline.py` | `scripts/maintenance/daily_pipeline.py` | 每日跑批 |
| `scripts/server_monitor.sh` | `scripts/monitor/server_monitor.sh` | 服务器监控 |
| `scripts/server_status.sh` | `scripts/monitor/server_status.sh` | 状态检查 |
| `scripts/health_vibe_research.sh` | `scripts/monitor/health_vibe_research.sh` | 健康检查 |
| `scripts/runtime_python.sh` | `scripts/maintenance/runtime_python.sh` | Python 辅助 |
| `scripts/deploy_dashboard.sh` | `scripts/deploy/deploy_dashboard.sh` | 部署仪表板 |
| `scripts/backup.sh` | `scripts/backup/backup.sh` | 备份 |
| `scripts/restore.sh` | `scripts/backup/restore.sh` | 恢复 |
| `scripts/dev.sh` | `scripts/dev/dev.sh` | 开发环境 |

### 完整映射表

```
# DEV
scripts/dev.sh → scripts/dev/dev.sh
scripts/test_*.py → scripts/dev/test_*.py
scripts/test_*.sh → scripts/dev/test_*.sh
scripts/smoke_market_context_runtime.py → scripts/dev/smoke_market_context_runtime.py
scripts/diagnose_*.py → scripts/dev/diagnose_*.py
scripts/check_frontend_audit.py → scripts/dev/check_frontend_audit.py
scripts/check_no_secrets.py → scripts/dev/check_no_secrets.py

# DEPLOY
scripts/deploy_*.sh → scripts/deploy/deploy_*.sh
scripts/install_*.sh → scripts/deploy/install_*.sh
scripts/init_server_runtime.sh → scripts/deploy/init_server_runtime.sh
scripts/release_task_entrypoint.sh → scripts/deploy/release_task_entrypoint.sh
scripts/write_release_manifest.py → scripts/deploy/write_release_manifest.py
scripts/check_release_consistency.py → scripts/deploy/check_release_consistency.py
scripts/preflight_upload.py → scripts/deploy/preflight_upload.py
scripts/sync_runtime_files_to_server.py → scripts/deploy/sync_runtime_files_to_server.py

# MONITOR
scripts/server_*.sh → scripts/monitor/server_*.sh
scripts/monitor_*.sh → scripts/monitor/monitor_*.sh
scripts/health_*.sh → scripts/monitor/health_*.sh
scripts/check_*.sh → scripts/monitor/check_*.sh
scripts/check_*.py → scripts/monitor/check_*.py
scripts/verify_*.py → scripts/monitor/verify_*.py
scripts/safe_probe.sh → scripts/monitor/safe_probe.sh
scripts/remote_runtime_probe.py → scripts/monitor/remote_runtime_probe.py
scripts/analyze_logs.py → scripts/monitor/analyze_logs.py

# BACKUP
scripts/backup.sh → scripts/backup/backup.sh
scripts/backup_verify.sh → scripts/backup/backup_verify.sh
scripts/restore.sh → scripts/backup/restore.sh
scripts/export_dashboard_db.py → scripts/backup/export_dashboard_db.py

# DATA
scripts/collect_*.py → scripts/data/collect_*.py
scripts/fetch_*.py → scripts/data/fetch_*.py
scripts/fetch_*.sh → scripts/data/fetch_*.sh
scripts/backfill_*.py → scripts/data/backfill_*.py
scripts/update_sqlite_daily.py → scripts/data/update_sqlite_daily.py
scripts/refresh_sqlite_batch.py → scripts/data/refresh_sqlite_batch.py
scripts/import_*.py → scripts/data/import_*.py
scripts/download_*.py → scripts/data/download_*.py
scripts/detect_sector.py → scripts/data/detect_sector.py
scripts/resolve_ticker.py → scripts/data/resolve_ticker.py
scripts/merge_*.py → scripts/data/merge_*.py
scripts/prepare_*.py → scripts/data/prepare_*.py
scripts/mark_*.py → scripts/data/mark_*.py
scripts/preload_*.sh → scripts/data/preload_*.sh
scripts/generate_cold_start_signals.py → scripts/data/generate_cold_start_signals.py

# MAINTENANCE
scripts/daily_*.sh → scripts/maintenance/daily_*.sh
scripts/daily_*.py → scripts/maintenance/daily_*.py
scripts/coldstart_*.sh → scripts/maintenance/coldstart_*.sh
scripts/bt_task.sh → scripts/maintenance/bt_task.sh
scripts/intraday_refresh.sh → scripts/maintenance/intraday_refresh.sh
scripts/midday_refresh.sh → scripts/maintenance/midday_refresh.sh
scripts/variant_refresh.sh → scripts/maintenance/variant_refresh.sh
scripts/news_catalysts.sh → scripts/maintenance/news_catalysts.sh
scripts/clear_locks.sh → scripts/maintenance/clear_locks.sh
scripts/cleanup_ledger.py → scripts/maintenance/cleanup_ledger.py
scripts/manage_data_lifecycle.py → scripts/maintenance/manage_data_lifecycle.py
scripts/repair_*.py → scripts/maintenance/repair_*.py
scripts/setup_log_rotation.sh → scripts/maintenance/setup_log_rotation.sh
scripts/runtime_python.sh → scripts/maintenance/runtime_python.sh

# PRODUCTION
scripts/server_sync_and_run.sh → scripts/production/server_sync_and_run.sh
scripts/start_*.sh → scripts/production/start_*.sh
scripts/stop_*.sh → scripts/production/stop_*.sh
scripts/rollback_*.sh → scripts/production/rollback_*.sh

# ANALYSIS
scripts/render_*.py → scripts/analysis/render_*.py
scripts/open_dashboard.py → scripts/analysis/open_dashboard.py
scripts/write_home_snapshot.py → scripts/analysis/write_home_snapshot.py
scripts/experiment_report.py → scripts/analysis/experiment_report.py
scripts/ic_diagnosis*.py → scripts/analysis/ic_diagnosis*.py
scripts/ic_diagnosis*.sh → scripts/analysis/ic_diagnosis*.sh
scripts/factor_ic_diagnosis.py → scripts/analysis/factor_ic_diagnosis.py
scripts/run_variant_suite.py → scripts/analysis/run_variant_suite.py
scripts/refresh_variant_results_*.py → scripts/analysis/refresh_variant_results_*.py
scripts/extract_*.py → scripts/analysis/extract_*.py
scripts/stop_loss_exit_evidence.py → scripts/analysis/stop_loss_exit_evidence.py

# RUNNER
scripts/runner_*.sh → scripts/runner/runner_*.sh
scripts/run_*_gate.py → scripts/runner/run_*_gate.py
scripts/gate_*.sh → scripts/runner/gate_*.sh
scripts/recover_*.sh → scripts/runner/recover_*.sh
scripts/sync_and_recover_*.sh → scripts/runner/sync_and_recover_*.sh
scripts/t3_*.sh → scripts/runner/t3_*.sh
scripts/build_t3_*.py → scripts/runner/build_t3_*.py
scripts/compare_t3_*.py → scripts/runner/compare_t3_*.py
scripts/promote_gate_sidecar.py → scripts/runner/promote_gate_sidecar.py

# DEBATE
scripts/generate_sample_debate.py → scripts/debate/generate_sample_debate.py
scripts/feedback_debate.py → scripts/debate/feedback_debate.py

# LAUNCHD (无变化)
scripts/launchd/* → scripts/launchd/* (保持不变)
```

---

## 🛠️ 迁移步骤

### 步骤 1: 创建向后兼容符号链接（推荐）

在 scripts/ 根目录创建符号链接，保持旧路径可用：

```bash
cd /opt/aqsp/scripts

# 高频脚本符号链接
ln -sf maintenance/bt_task.sh bt_task.sh
ln -sf maintenance/daily_pipeline.sh daily_pipeline.sh
ln -sf maintenance/daily_pipeline.py daily_pipeline.py
ln -sf monitor/server_monitor.sh server_monitor.sh
ln -sf monitor/server_status.sh server_status.sh
ln -sf monitor/health_vibe_research.sh health_vibe_research.sh
ln -sf maintenance/runtime_python.sh runtime_python.sh
ln -sf deploy/deploy_dashboard.sh deploy_dashboard.sh
ln -sf backup/backup.sh backup.sh
ln -sf backup/restore.sh restore.sh
ln -sf dev/dev.sh dev.sh

# 验证符号链接
ls -l *.sh 2>/dev/null | grep "\->"
```

**优点**: 现有 cron 任务和配置无需修改  
**缺点**: 随着时间推移可能积累冗余链接

### 步骤 2: 更新 Systemd 服务

编辑 `/deploy/systemd/*.service` 文件：

```bash
cd /opt/aqsp/deploy/systemd

# 备份
cp aqsp-vibe-research-api.service aqsp-vibe-research-api.service.bak
cp aqsp-vibe-research-preview.service aqsp-vibe-research-preview.service.bak

# 更新路径 (手动编辑或使用 sed)
sed -i 's|scripts/health_vibe_research.sh|scripts/monitor/health_vibe_research.sh|g' \
    aqsp-vibe-research-api.service \
    aqsp-vibe-research-preview.service

# 重新加载 systemd
sudo systemctl daemon-reload

# 验证配置
systemctl cat aqsp-vibe-research-api.service | grep health_vibe_research
```

### 步骤 3: 更新 Cron 任务

检查并更新 crontab：

```bash
# 查看当前 cron 任务
crontab -l > /tmp/crontab.bak

# 编辑 cron 任务
crontab -e
```

**需要更新的示例**:

```cron
# 旧
0 18 * * 1-5 /opt/aqsp/scripts/bt_task.sh daily
*/15 * * * * /opt/aqsp/scripts/server_monitor.sh

# 新
0 18 * * 1-5 /opt/aqsp/scripts/maintenance/bt_task.sh daily
*/15 * * * * /opt/aqsp/scripts/monitor/server_monitor.sh

# 或使用符号链接（推荐）
0 18 * * 1-5 /opt/aqsp/scripts/bt_task.sh daily
*/15 * * * * /opt/aqsp/scripts/server_monitor.sh
```

### 步骤 4: 更新脚本内部引用

许多脚本引用 `runtime_python.sh`，需要更新路径：

```bash
cd /opt/aqsp/scripts

# 查找引用
grep -r "scripts/runtime_python.sh" . --include="*.sh" --include="*.py"

# 批量更新（谨慎使用）
find . -type f \( -name "*.sh" -o -name "*.py" \) -exec \
    sed -i 's|scripts/runtime_python.sh|scripts/maintenance/runtime_python.sh|g' {} \;
```

**或者**，在 `runtime_python.sh` 顶部添加兼容性处理：

```bash
# 在 maintenance/runtime_python.sh 顶部添加
if [ -L "${BASH_SOURCE[0]}" ]; then
    # 通过符号链接调用，调整路径
    SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
```

### 步骤 5: 更新宝塔面板配置

登录宝塔面板，更新计划任务：

1. 登录面板: `https://your-server:8888`
2. 进入 "计划任务"
3. 编辑 AQSP 相关任务
4. 更新脚本路径：
   - `scripts/bt_task.sh` → `scripts/maintenance/bt_task.sh`

**或者** 使用符号链接，无需修改。

### 步骤 6: 更新 launchd 配置（macOS）

编辑 `~/Library/LaunchAgents/com.aqsp.*.plist`：

```bash
cd ~/Library/LaunchAgents

# 备份
cp com.aqsp.morning.plist com.aqsp.morning.plist.bak

# 编辑
vi com.aqsp.morning.plist
```

**更新示例**:

```xml
<!-- 旧 -->
<string>/Users/ivon/Documents/AI量化选股/scripts/launchd/aqsp_morning_wrapper.sh</string>

<!-- 新（无需更改，launchd 目录保持不变）-->
<string>/Users/ivon/Documents/AI量化选股/scripts/launchd/aqsp_morning_wrapper.sh</string>
```

launchd 目录下的脚本路径未改变，但如果 wrapper 内部调用其他脚本，需要检查：

```bash
cd /opt/aqsp/scripts/launchd
grep "scripts/" *.sh
```

### 步骤 7: 验证迁移

运行健康检查脚本：

```bash
cd /opt/aqsp

# 验证核心脚本可执行
scripts/maintenance/bt_task.sh status
scripts/monitor/server_status.sh
scripts/monitor/health_vibe_research.sh --check-only

# 验证符号链接（如果创建了）
ls -l scripts/*.sh 2>/dev/null | grep "\->"

# 验证 systemd 服务
sudo systemctl status aqsp-vibe-research-api --no-pager
sudo systemctl status aqsp-vibe-research-preview --no-pager

# 验证 cron 任务（手动触发）
/opt/aqsp/scripts/maintenance/bt_task.sh monitor
```

---

## 🔍 需要检查的文件列表

### 配置文件

- [ ] `/etc/cron.d/aqsp*`
- [ ] `/etc/systemd/system/aqsp*.service`
- [ ] `~/Library/LaunchAgents/com.aqsp.*.plist`
- [ ] 宝塔面板计划任务配置

### 脚本文件

- [ ] `scripts/launchd/*.sh` - 检查内部调用路径
- [ ] `scripts/maintenance/bt_task.sh` - 检查调用其他脚本的路径
- [ ] `scripts/maintenance/daily_pipeline.sh` - 检查 runtime_python.sh 路径
- [ ] `scripts/deploy/deploy_*.sh` - 检查调用其他脚本的路径
- [ ] 自定义脚本中的 `source` 或 `./` 调用

### 文档

- [ ] `README.md`
- [ ] `deploy/bt_panel_setup.md`
- [ ] `deploy/setup.sh`
- [ ] `CHANGELOG.md`

---

## ⚠️ 常见问题

### Q1: Cron 任务报错 "No such file or directory"

**原因**: Cron 任务仍使用旧路径

**解决**:
1. 创建符号链接（快速）
2. 或更新 crontab 中的路径（彻底）

### Q2: Systemd 服务启动失败

**原因**: ExecStartPre 路径未更新

**解决**:
```bash
# 编辑服务文件
sudo vi /etc/systemd/system/aqsp-vibe-research-api.service

# 更新路径
# 旧: ExecStartPre=.../scripts/health_vibe_research.sh
# 新: ExecStartPre=.../scripts/monitor/health_vibe_research.sh

# 重新加载
sudo systemctl daemon-reload
sudo systemctl restart aqsp-vibe-research-api
```

### Q3: 脚本内部引用 runtime_python.sh 失败

**原因**: 脚本使用相对路径 `../runtime_python.sh`

**解决**:
```bash
# 在脚本中使用绝对路径
RUNTIME_PYTHON_HELPER="${PROJECT_ROOT}/scripts/maintenance/runtime_python.sh"

# 或创建符号链接
cd /opt/aqsp/scripts
ln -sf maintenance/runtime_python.sh runtime_python.sh
```

### Q4: Git 操作被 index.lock 阻塞

**原因**: Git 索引文件被锁定

**解决**:
```bash
rm -f /opt/aqsp/.git/index.lock
```

---

## 📊 迁移检查清单

使用此清单跟踪迁移进度：

- [ ] **步骤 1**: 创建符号链接
  - [ ] bt_task.sh
  - [ ] daily_pipeline.sh
  - [ ] server_monitor.sh
  - [ ] health_vibe_research.sh
  - [ ] runtime_python.sh

- [ ] **步骤 2**: 更新 Systemd 服务
  - [ ] aqsp-vibe-research-api.service
  - [ ] aqsp-vibe-research-preview.service
  - [ ] daemon-reload 已执行

- [ ] **步骤 3**: 更新 Cron 任务
  - [ ] 备份 crontab
  - [ ] 更新路径或确认符号链接有效
  - [ ] 手动测试触发

- [ ] **步骤 4**: 更新脚本内部引用
  - [ ] 搜索 runtime_python.sh 引用
  - [ ] 批量更新或创建符号链接

- [ ] **步骤 5**: 更新宝塔面板
  - [ ] 登录面板
  - [ ] 更新计划任务路径

- [ ] **步骤 6**: 更新 launchd (macOS)
  - [ ] 检查 wrapper 脚本内部调用
  - [ ] 如需更新，unload → 编辑 → load

- [ ] **步骤 7**: 验证迁移
  - [ ] 运行 bt_task.sh status
  - [ ] 运行 server_status.sh
  - [ ] 运行 health_vibe_research.sh
  - [ ] 检查 systemd 服务状态
  - [ ] 手动触发 cron 任务测试

---

## 🚀 回滚计划

如果迁移出现问题，可以快速回滚：

```bash
cd /opt/aqsp/scripts

# 1. 删除所有符号链接
find . -maxdepth 1 -type l -delete

# 2. 将所有脚本移回根目录
find dev deploy monitor backup data maintenance production analysis runner debate \
    -type f \( -name "*.sh" -o -name "*.py" \) \
    -exec mv {} . \;

# 3. 恢复配置文件
cp /path/to/backup/crontab /tmp/crontab.restore
crontab /tmp/crontab.restore

# 4. 恢复 systemd 服务
cp /path/to/backup/*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

**建议**: 在生产环境迁移前，先在测试环境验证。

---

## 📞 支持

如遇到迁移问题，请：

1. 检查本文档的常见问题部分
2. 查看 `scripts/README.md` 了解新目录结构
3. 提交 Issue 附带错误日志

---

**迁移完成后，请更新此日期**: _______________
