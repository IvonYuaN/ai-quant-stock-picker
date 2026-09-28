# 数据备份与恢复系统

完整的数据备份、恢复和验证解决方案，适用于 AI量化选股项目。

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [脚本说明](#脚本说明)
- [使用示例](#使用示例)
- [自动化配置](#自动化配置)
- [最佳实践](#最佳实践)
- [故障排除](#故障排除)

## 功能特性

### backup.sh - 备份脚本
- ✓ 增量备份（基于 rsync）
- ✓ 自动压缩（gzip，可配置压缩级别）
- ✓ 时间戳命名
- ✓ 本地和远程备份支持
- ✓ 自动清理旧备份（保留 N 个版本）
- ✓ 完整的日志记录
- ✓ 演练模式（--dry-run）

### restore.sh - 恢复脚本
- ✓ 列出所有可用备份
- ✓ 指定版本或最新版本恢复
- ✓ 恢复前完整性验证
- ✓ 自动创建恢复点
- ✓ 支持回滚操作
- ✓ 交互式确认机制

### backup_verify.sh - 验证脚本
- ✓ 验证备份文件完整性
- ✓ 检查文件数量和大小
- ✓ 对比元数据与实际内容
- ✓ 生成详细验证报告
- ✓ 支持批量验证

## 快速开始

### 1. 确认安装

所有脚本已经配置为可执行：

```bash
cd /Users/ivon/Documents/AI量化选股
ls -lh scripts/backup*.sh scripts/restore.sh
```

### 2. 创建第一个备份

```bash
# 基本备份
./scripts/backup.sh

# 查看帮助
./scripts/backup.sh --help
```

### 3. 列出可用备份

```bash
./scripts/restore.sh --list
```

### 4. 验证备份

```bash
# 验证最新备份
./scripts/backup_verify.sh --backup backup-20260928-120000.tar.gz

# 验证所有备份
./scripts/backup_verify.sh --all
```

## 脚本说明

### backup.sh

**用途**: 创建数据备份

**基本语法**:
```bash
./scripts/backup.sh [OPTIONS]
```

**选项**:
- `--backup-dir DIR` - 备份目标目录（默认: `$PROJECT_ROOT/backups`）
- `--remote USER@HOST:PATH` - 远程备份位置
- `--keep-versions N` - 保留最近 N 个版本（默认: 10）
- `--compress-level N` - 压缩级别 0-9（默认: 6）
- `--dry-run` - 演练模式，不实际执行
- `--help` - 显示帮助信息

**备份内容**:
- `data/predictions.jsonl`
- `data/paper_trades.jsonl`
- `data/ledger.jsonl`
- `data/weight_history.jsonl`
- `data/pick_snapshots.jsonl`
- `data/debate_results.jsonl`
- `data/llm_calls.jsonl`
- `reports/` 目录
- `config/` 目录

**环境变量**:
- `AQSP_PROJECT_ROOT` - 项目根目录
- `AQSP_BACKUP_DIR` - 备份目录
- `AQSP_BACKUP_REMOTE` - 远程备份位置
- `AQSP_BACKUP_KEEP` - 保留版本数
- `AQSP_BACKUP_LOG_DIR` - 日志目录

### restore.sh

**用途**: 从备份恢复数据

**基本语法**:
```bash
./scripts/restore.sh [OPTIONS]
```

**选项**:
- `--list` - 列出所有可用备份
- `--backup FILE` - 指定要恢复的备份文件
- `--latest` - 恢复最新备份
- `--backup-dir DIR` - 备份目录
- `--restore-point NAME` - 恢复点名称
- `--skip-restore-point` - 跳过创建恢复点
- `--force` - 强制恢复，不确认
- `--verify-only` - 仅验证备份，不恢复
- `--rollback` - 回滚到最近的恢复点
- `--help` - 显示帮助信息

**环境变量**:
- `AQSP_PROJECT_ROOT` - 项目根目录
- `AQSP_BACKUP_DIR` - 备份目录
- `AQSP_RESTORE_LOG_DIR` - 日志目录

### backup_verify.sh

**用途**: 验证备份完整性

**基本语法**:
```bash
./scripts/backup_verify.sh [OPTIONS]
```

**选项**:
- `--backup FILE` - 验证指定备份文件
- `--all` - 验证所有备份文件
- `--backup-dir DIR` - 备份目录
- `--report FILE` - 报告输出文件
- `--quiet` - 静默模式，仅输出错误
- `--help` - 显示帮助信息

**环境变量**:
- `AQSP_PROJECT_ROOT` - 项目根目录
- `AQSP_BACKUP_DIR` - 备份目录
- `AQSP_VERIFY_LOG_DIR` - 日志目录

## 使用示例

### 备份操作

```bash
# 1. 标准备份
./scripts/backup.sh

# 2. 自定义备份目录
./scripts/backup.sh --backup-dir /external/backup

# 3. 远程备份
./scripts/backup.sh --remote user@backup-server:/backup/aqsp

# 4. 保留更多版本
./scripts/backup.sh --keep-versions 20

# 5. 高压缩率备份（更慢，但文件更小）
./scripts/backup.sh --compress-level 9

# 6. 演练模式（查看将要备份的内容）
./scripts/backup.sh --dry-run

# 7. 组合选项
./scripts/backup.sh --backup-dir /external/backup --keep-versions 15 --compress-level 7
```

### 恢复操作

```bash
# 1. 列出所有可用备份
./scripts/restore.sh --list

# 2. 恢复最新备份
./scripts/restore.sh --latest

# 3. 恢复指定备份
./scripts/restore.sh --backup backup-20260928-120000.tar.gz

# 4. 仅验证备份，不恢复
./scripts/restore.sh --backup backup-20260928-120000.tar.gz --verify-only

# 5. 恢复但不创建恢复点（不推荐）
./scripts/restore.sh --latest --skip-restore-point

# 6. 强制恢复，跳过确认
./scripts/restore.sh --latest --force

# 7. 回滚到最近的恢复点
./scripts/restore.sh --rollback

# 8. 自定义恢复点名称
./scripts/restore.sh --latest --restore-point "before-major-update"
```

### 验证操作

```bash
# 1. 验证单个备份
./scripts/backup_verify.sh --backup backup-20260928-120000.tar.gz

# 2. 验证所有备份
./scripts/backup_verify.sh --all

# 3. 验证并生成报告
./scripts/backup_verify.sh --all --report reports/backup_verify_report.txt

# 4. 静默模式验证（仅显示错误）
./scripts/backup_verify.sh --backup backup-20260928-120000.tar.gz --quiet

# 5. 使用简短名称验证
./scripts/backup_verify.sh --backup backup-20260928-120000
```

## 自动化配置

### 使用 Cron（Linux/macOS）

参考配置文件: `config/backup_cron.example`

```bash
# 1. 编辑 crontab
crontab -e

# 2. 添加每日备份任务（每天凌晨 2:00）
0 2 * * * cd /Users/ivon/Documents/AI量化选股 && bash scripts/backup.sh >> logs/backup/cron.log 2>&1

# 3. 添加每周验证任务（周日凌晨 3:00）
0 3 * * 0 cd /Users/ivon/Documents/AI量化选股 && bash scripts/backup_verify.sh --all >> logs/verify/cron.log 2>&1

# 4. 查看当前 cron 任务
crontab -l
```

### 使用 LaunchAgent（macOS 推荐）

创建文件: `~/Library/LaunchAgents/com.aqsp.backup.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aqsp.backup</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/ivon/Documents/AI量化选股/scripts/backup.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>2</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/ivon/Documents/AI量化选股/logs/backup/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/ivon/Documents/AI量化选股/logs/backup/launchd.error.log</string>
</dict>
</plist>
```

加载和管理：

```bash
# 加载
launchctl load ~/Library/LaunchAgents/com.aqsp.backup.plist

# 卸载
launchctl unload ~/Library/LaunchAgents/com.aqsp.backup.plist

# 立即运行
launchctl start com.aqsp.backup

# 查看状态
launchctl list | grep aqsp
```

## 最佳实践

### 1. 备份策略

**推荐的备份频率**:
- 生产环境: 每 4-6 小时备份一次
- 开发环境: 每天备份一次
- 测试环境: 每周备份一次

**3-2-1 备份原则**:
- 保持 3 份数据副本
- 使用 2 种不同的存储介质
- 至少 1 份异地备份

```bash
# 本地备份
./scripts/backup.sh --keep-versions 10

# 远程备份
./scripts/backup.sh --remote user@remote-server:/backup/aqsp --keep-versions 30
```

### 2. 验证备份

定期验证备份完整性:

```bash
# 每天验证最新备份
./scripts/backup_verify.sh --backup $(ls -t backups/backup-*.tar.gz | head -1) --quiet

# 每周验证所有备份
./scripts/backup_verify.sh --all --report reports/weekly_backup_report.txt
```

### 3. 恢复演练

定期进行恢复演练，确保在紧急情况下能够快速恢复:

```bash
# 在测试环境中练习恢复
./scripts/restore.sh --list
./scripts/restore.sh --latest --verify-only
```

### 4. 监控和告警

监控备份状态:

```bash
# 检查最近的备份
ls -lth backups/ | head -5

# 检查备份日志
tail -50 logs/backup/backup-*.log

# 检查磁盘空间
df -h backups/
```

### 5. 安全性

```bash
# 设置备份目录权限
chmod 700 backups/

# 加密敏感备份（可选）
gpg --symmetric --cipher-algo AES256 backups/backup-20260928-120000.tar.gz
```

## 故障排除

### 问题 1: 备份脚本权限不足

**症状**: `Permission denied`

**解决**:
```bash
chmod +x scripts/backup.sh scripts/restore.sh scripts/backup_verify.sh
```

### 问题 2: 磁盘空间不足

**症状**: `No space left on device`

**解决**:
```bash
# 检查磁盘使用
df -h

# 清理旧备份
./scripts/backup.sh --keep-versions 5

# 手动删除旧备份
rm backups/backup-old-*.tar.gz
```

### 问题 3: 远程备份失败

**症状**: `rsync: connection failed`

**解决**:
```bash
# 测试 SSH 连接
ssh user@remote-server

# 配置 SSH 密钥认证
ssh-keygen -t rsa
ssh-copy-id user@remote-server

# 测试 rsync
rsync -avz test.txt user@remote-server:/backup/test/
```

### 问题 4: 备份文件损坏

**症状**: 验证失败

**解决**:
```bash
# 验证备份
./scripts/backup_verify.sh --backup backup-20260928-120000.tar.gz

# 如果损坏，使用更早的备份
./scripts/restore.sh --list
./scripts/restore.sh --backup backup-20260927-120000.tar.gz
```

### 问题 5: Cron 任务未执行

**症状**: 备份没有自动运行

**解决**:
```bash
# 检查 cron 日志
tail -50 logs/backup/cron.log

# 检查 cron 服务状态（Linux）
systemctl status cron

# 检查 LaunchAgent 状态（macOS）
launchctl list | grep aqsp

# 手动测试脚本
./scripts/backup.sh
```

### 问题 6: 恢复后数据不完整

**症状**: 某些文件缺失

**解决**:
```bash
# 验证备份内容
./scripts/backup_verify.sh --backup backup-20260928-120000.tar.gz

# 查看备份清单
tar -tzf backups/backup-20260928-120000.tar.gz | grep -E "(data|config|reports)"

# 回滚并尝试其他备份
./scripts/restore.sh --rollback
./scripts/restore.sh --backup backup-20260927-120000.tar.gz
```

## 日志位置

- 备份日志: `logs/backup/backup-TIMESTAMP.log`
- 恢复日志: `logs/restore/restore-TIMESTAMP.log`
- 验证日志: `logs/verify/verify-TIMESTAMP.log`
- Cron 日志: `logs/backup/cron.log`, `logs/verify/cron.log`

## 文件结构

```
AI量化选股/
├── scripts/
│   ├── backup.sh              # 备份脚本
│   ├── restore.sh             # 恢复脚本
│   └── backup_verify.sh       # 验证脚本
├── config/
│   └── backup_cron.example    # Cron 配置示例
├── backups/                   # 备份存储目录
│   ├── backup-20260928-120000.tar.gz
│   ├── backup-20260927-120000.tar.gz
│   └── .restore_points/       # 恢复点目录
│       └── restore_point-20260928-120000/
├── logs/
│   ├── backup/               # 备份日志
│   ├── restore/              # 恢复日志
│   └── verify/               # 验证日志
└── docs/
    └── BACKUP_SYSTEM.md      # 本文档
```

## 技术细节

### 备份格式

- 压缩格式: tar.gz
- 默认压缩级别: 6
- 包含元数据和清单文件
- 时间戳格式: YYYYMMDD-HHMMSS

### 增量备份

使用 rsync 的特性:
- 只复制变化的文件
- 保留文件属性和时间戳
- 支持断点续传

### 恢复点机制

- 恢复前自动创建当前状态快照
- 保留最近 5 个恢复点
- 支持一键回滚

## 支持和反馈

如有问题或建议，请:
1. 检查日志文件
2. 参考故障排除部分
3. 联系系统管理员

---

**版本**: 1.0.0  
**更新日期**: 2026-09-28  
**维护者**: AQSP Team
