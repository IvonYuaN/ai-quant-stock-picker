# scripts/ - 脚本目录重组说明

> **重组日期**: 2026-09-28  
> **目的**: 明确职责边界，便于查找和维护

---

## 📁 目录结构

```
scripts/
├── dev/              # 开发和测试工具
├── deploy/           # 部署和安装脚本
├── monitor/          # 监控和健康检查
├── backup/           # 备份和恢复
├── data/             # 数据采集和处理
├── maintenance/      # 日常维护和定时任务
├── production/       # 生产环境启动/停止
├── analysis/         # 分析和报告生成
├── runner/           # Walk-forward gate 运行器
├── debate/           # 辩论系统相关
├── launchd/          # macOS 调度包装脚本
└── legacy/           # 待整理的旧脚本
```

---

## 📖 各目录用途详解

### dev/ - 开发工具
**用途**: 本地开发、测试、调试工具

**常用脚本**:
- `dev.sh` - 开发环境一键启动（FastAPI + Vite）
- `test_*.py/sh` - 各类单元测试和集成测试
- `smoke_market_context_runtime.py` - 市场上下文运行时冒烟测试
- `diagnose_runtime.py` - 运行时诊断工具
- `example_fetcher_usage.py` - 数据获取器使用示例

**何时使用**: 本地开发、功能验证、问题排查

---

### deploy/ - 部署相关
**用途**: 服务器部署、环境初始化、cron/systemd 安装

**常用脚本**:
- `deploy_dashboard.sh` - 部署仪表板到远程服务器
- `deploy_immutable_release.sh` - 不可变发布部署
- `install_server_cron.sh` - 安装服务器端定时任务
- `install_vibe_research_systemd.sh` - 安装 vibe-research systemd 服务
- `init_server_runtime.sh` - 初始化服务器运行时环境
- `sync_runtime_files_to_server.py` - 同步运行时文件到服务器

**何时使用**: 首次部署、服务更新、环境配置变更

**依赖关系**:
```
deploy_immutable_release.sh
  ↓
write_release_manifest.py
  ↓
check_release_consistency.py
```

---

### monitor/ - 监控和健康检查
**用途**: 系统健康监控、状态检查、异常告警

**常用脚本**:
- `server_monitor.sh` - 服务器监控主脚本（cron 定时运行）
- `server_status.sh` - 服务器状态快速检查
- `health_vibe_research.sh` - Vibe Research 服务健康检查
- `check_scheduler.py` - 调度器状态检查
- `check_before_live.py` - 上线前完整性检查（100+ 项）
- `server_doctor.py` - 服务器问题诊断和修复建议
- `analyze_logs.py` - 日志分析工具

**何时使用**: 定时监控、问题排查、上线前检查

**Cron 配置示例**:
```bash
*/15 * * * * /opt/aqsp/scripts/monitor/server_monitor.sh
```

---

### backup/ - 备份和恢复
**用途**: 数据备份、验证、恢复

**常用脚本**:
- `backup.sh` - 增量备份关键数据（data/*.jsonl, reports/, config/）
- `backup_verify.sh` - 验证备份完整性
- `restore.sh` - 从备份恢复数据
- `export_dashboard_db.py` - 导出仪表板数据库

**用法示例**:
```bash
# 备份到本地
./backup/backup.sh --backup-dir /mnt/backups --keep-versions 10

# 备份到远程
./backup/backup.sh --remote user@host:/backup/aqsp

# 验证备份
./backup/backup_verify.sh /mnt/backups/backup-20260928-180000.tar.gz

# 恢复
./backup/restore.sh /mnt/backups/backup-20260928-180000.tar.gz
```

**备份内容**:
- `data/predictions.jsonl`
- `data/paper_trades.jsonl`
- `data/ledger.jsonl`
- `data/weight_history.jsonl`
- `reports/`
- `config/`

---

### data/ - 数据采集和处理
**用途**: 股票数据获取、数据库更新、数据转换

**数据采集脚本**:
- `collect_stock_data.py` - 单只股票全量数据采集
- `fetch_cls_news.py` - 财联社新闻
- `fetch_concept_board.py` - 概念板块
- `fetch_event_data.py` - 事件数据
- `fetch_longhubang.py` - 龙虎榜数据
- `preload_event_data.sh` - 预加载事件数据

**数据回填脚本**:
- `backfill_akshare.py` - AKShare 数据回填
- `backfill_index_to_sqlite.py` - 指数数据回填
- `backfill_intraday_debate.py` - 盘中辩论数据回填

**数据库维护**:
- `update_sqlite_daily.py` - 每日 SQLite 更新
- `refresh_sqlite_batch.py` - 批量刷新 SQLite

**工具脚本**:
- `resolve_ticker.py` - 中文股票名 → 6位代码解析
- `detect_sector.py` - 板块/概念识别

**用法示例**:
```bash
# 采集单只股票数据
python3 data/collect_stock_data.py 688017

# 解析股票名称
python3 data/resolve_ticker.py 贵州茅台

# 识别板块
python3 data/detect_sector.py 600519
```

---

### maintenance/ - 日常维护
**用途**: 定时任务、数据生命周期管理、日志轮转

**核心脚本**:
- `bt_task.sh` - 宝塔面板任务统一入口
- `daily_pipeline.sh` / `daily_pipeline.py` - 每日跑批主链路
- `coldstart_daily.sh` - 冷启动补样本
- `intraday_refresh.sh` - 盘中刷新
- `midday_refresh.sh` - 午盘分析
- `news_catalysts.sh` - 消息面雷达
- `variant_refresh.sh` - 变体刷新

**维护工具**:
- `clear_locks.sh` - 清理锁文件
- `cleanup_ledger.py` - 清理账本
- `manage_data_lifecycle.py` - 数据生命周期管理
- `setup_log_rotation.sh` - 配置日志轮转
- `runtime_python.sh` - 运行时 Python 解析器辅助函数

**Cron 配置示例**:
```bash
# 每日跑批
0 18 * * 1-5 /opt/aqsp/scripts/maintenance/bt_task.sh daily

# 盘中刷新
*/10 9-15 * * 1-5 /opt/aqsp/scripts/maintenance/bt_task.sh intraday

# 消息面雷达
45 8 * * 1-5 /opt/aqsp/scripts/maintenance/bt_task.sh news
```

---

### production/ - 生产环境运行
**用途**: 服务启动、停止、同步、回滚

**常用脚本**:
- `start_dashboard.sh` - 启动仪表板
- `start_vibe_research.sh` - 启动 Vibe Research（前后端）
- `start_vibe_research_service.sh` - Systemd 服务启动包装
- `stop_vibe_research_service.sh` - 停止服务
- `rollback_vibe_research.sh` - 回滚到上一版本
- `server_sync_and_run.sh` - 同步并运行

**用法示例**:
```bash
# 启动服务
./production/start_vibe_research.sh

# 停止服务
sudo systemctl stop aqsp-vibe-research-api
sudo systemctl stop aqsp-vibe-research-preview

# 回滚
./production/rollback_vibe_research.sh
```

---

### analysis/ - 分析和报告
**用途**: 数据分析、仪表板生成、因子诊断

**仪表板和可视化**:
- `render_dashboard.py` - 生成主仪表板
- `render_agent_dashboard.py` - Agent 仪表板
- `open_dashboard.py` - 打开仪表板
- `write_home_snapshot.py` - 生成首页快照

**因子和策略分析**:
- `ic_diagnosis.py` - IC 诊断
- `factor_ic_diagnosis.py` - 因子 IC 诊断
- `run_variant_suite.py` - 变体套件运行
- `experiment_report.py` - 实验报告生成

**证据提取**:
- `extract_gate_summary.py` - Gate 摘要提取
- `stop_loss_exit_evidence.py` - 止损退出证据

**用法示例**:
```bash
# 生成仪表板
python3 analysis/render_dashboard.py

# IC 诊断
python3 analysis/ic_diagnosis.py --start-date 2024-01-01
```

---

### runner/ - Walk-forward Gate 运行器
**用途**: Walk-forward 测试、回测、gate 控制

**核心脚本**:
- `run_production_walkforward_gate.py` - 生产环境 walk-forward gate
- `run_dual_window_gate.py` - 双窗口 gate
- `runner_fetch.sh` - 获取运行器状态
- `runner_gate.sh` - Gate 控制
- `runner_sync.sh` - 同步运行器

**恢复和管理**:
- `recover_walkforward_incident.sh` - 恢复 walk-forward 事故
- `gate_finalize.sh` - Gate 完成
- `promote_gate_sidecar.py` - 提升 gate sidecar

**T3 系列**（特定实验）:
- `t3_parallel_gate.sh`
- `t3_relaunch_gate.sh`
- `t3_resume_runner.sh`
- `t3_wf001_3y_resume.sh`

**依赖关系**:
```
run_production_walkforward_gate.py
  ↓
runner_gate.sh → gate_finalize.sh
  ↓
runner_sync.sh
```

---

### debate/ - 辩论系统
**用途**: 辩论样本生成、反馈收集

**常用脚本**:
- `generate_sample_debate.py` - 生成辩论样本
- `feedback_debate.py` - 辩论反馈收集

---

### launchd/ - macOS 调度包装
**用途**: macOS launchd 定时任务包装脚本

**脚本**:
- `aqsp_daily_run_wrapper.sh` - 每日任务包装
- `aqsp_morning_wrapper.sh` - 早盘任务包装
- `aqsp_closing_wrapper.sh` - 收盘任务包装

**配置文件**:
- `com.aqsp.daily.plist`
- `com.aqsp.morning.plist`
- `com.aqsp.closing.plist`

**安装示例**:
```bash
cp scripts/launchd/com.aqsp.morning.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aqsp.morning.plist
```

---

## 🔗 外部引用位置

### Systemd 服务
- `/deploy/systemd/aqsp-vibe-research-api.service`
  - 引用: `scripts/monitor/health_vibe_research.sh`
- `/deploy/systemd/aqsp-vibe-research-preview.service`
  - 引用: `scripts/monitor/health_vibe_research.sh`

### 宝塔面板
- `/deploy/bt_panel_setup.md`
  - 引用: `scripts/maintenance/bt_task.sh`

### Cron 任务
检查这些位置的 crontab 配置:
```bash
# 服务器 crontab
crontab -l

# 系统 cron
ls /etc/cron.d/aqsp*
```

**可能需要更新的路径**:
- `scripts/server_monitor.sh` → `scripts/monitor/server_monitor.sh`
- `scripts/bt_task.sh` → `scripts/maintenance/bt_task.sh`
- `scripts/daily_pipeline.sh` → `scripts/maintenance/daily_pipeline.sh`

---

## 🔧 迁移注意事项

### 1. 脚本内部引用
许多脚本会调用 `runtime_python.sh`，已移动到 `maintenance/` 目录。

**受影响的脚本**: 所有需要获取 Python 解释器的脚本

**修复方案**:
```bash
# 旧路径
RUNTIME_PYTHON_HELPER="${PROJECT_ROOT}/scripts/runtime_python.sh"

# 新路径（需要更新）
RUNTIME_PYTHON_HELPER="${PROJECT_ROOT}/scripts/maintenance/runtime_python.sh"
```

### 2. Systemd 服务路径
**文件**: `/deploy/systemd/*.service`

**需要更新**:
```ini
# 旧
ExecStartPre=@AQSP_PROJECT_ROOT@/scripts/health_vibe_research.sh

# 新
ExecStartPre=@AQSP_PROJECT_ROOT@/scripts/monitor/health_vibe_research.sh
```

### 3. Cron 任务路径
**建议**: 创建符号链接保持向后兼容

```bash
cd /opt/aqsp/scripts
ln -s maintenance/bt_task.sh bt_task.sh
ln -s monitor/server_monitor.sh server_monitor.sh
ln -s maintenance/daily_pipeline.sh daily_pipeline.sh
```

---

## 📊 脚本统计

| 目录 | Shell 脚本 | Python 脚本 | 总计 |
|------|-----------|------------|------|
| dev/ | 3 | 8 | 11 |
| deploy/ | 7 | 6 | 13 |
| monitor/ | 6 | 10 | 16 |
| backup/ | 3 | 1 | 4 |
| data/ | 2 | 22 | 24 |
| maintenance/ | 10 | 5 | 15 |
| production/ | 6 | 0 | 6 |
| analysis/ | 1 | 11 | 12 |
| runner/ | 11 | 5 | 16 |
| debate/ | 0 | 2 | 2 |
| launchd/ | 3 | 0 | 3 |
| **总计** | **52** | **70** | **122** |

---

## 🚀 常用操作快速索引

### 开发调试
```bash
# 启动开发环境
./dev/dev.sh

# 运行时诊断
python3 dev/diagnose_runtime.py

# 冒烟测试
python3 dev/smoke_market_context_runtime.py
```

### 部署上线
```bash
# 1. 预检查
python3 monitor/check_before_live.py

# 2. 部署
./deploy/deploy_immutable_release.sh

# 3. 健康检查
./monitor/health_vibe_research.sh

# 4. 监控日志
python3 monitor/analyze_logs.py
```

### 数据维护
```bash
# 每日数据更新
python3 data/update_sqlite_daily.py

# 批量刷新
python3 data/refresh_sqlite_batch.py

# 数据生命周期清理
python3 maintenance/manage_data_lifecycle.py
```

### 备份恢复
```bash
# 创建备份
./backup/backup.sh --keep-versions 10

# 验证备份
./backup/backup_verify.sh /path/to/backup.tar.gz

# 恢复数据
./backup/restore.sh /path/to/backup.tar.gz
```

### 问题排查
```bash
# 服务器状态
./monitor/server_status.sh

# 服务器诊断
python3 monitor/server_doctor.py

# 日志分析
python3 monitor/analyze_logs.py --last 24h

# 检查调度器
python3 monitor/check_scheduler.py
```

---

## 🗑️ 弃用脚本

目前无明确弃用脚本。如有发现不再使用的脚本，请移动到 `legacy/` 目录并在此记录。

**弃用标准**:
- 90 天内无调用记录
- 功能已被其他脚本替代
- 相关服务已下线

---

## 📝 维护日志

| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-09-28 | 目录重组 | 按功能分类，建立新目录结构 |

---

## 📮 反馈与建议

如发现:
- 脚本分类不当
- 路径引用错误
- 缺失关键脚本

请提交 Issue 或直接修改此文档。
