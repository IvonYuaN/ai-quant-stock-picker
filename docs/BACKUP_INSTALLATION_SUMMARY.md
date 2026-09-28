# 备份系统安装摘要

## 安装完成 ✓

数据备份与恢复系统已成功部署到项目中。

## 已创建的文件

### 核心脚本（已设置可执行权限）
- ✓ `scripts/backup.sh` (12KB) - 备份脚本
- ✓ `scripts/restore.sh` (15KB) - 恢复脚本  
- ✓ `scripts/backup_verify.sh` (13KB) - 验证脚本

### 配置文件
- ✓ `config/backup_cron.example` (7.5KB) - Cron 和 LaunchAgent 配置示例

### 文档
- ✓ `docs/BACKUP_SYSTEM.md` (12KB) - 完整系统文档
- ✓ `docs/BACKUP_QUICK_REFERENCE.md` (1.5KB) - 快速参考指南

### 目录结构
- ✓ `backups/` - 备份存储目录
- ✓ `logs/backup/` - 备份日志目录
- ✓ `logs/restore/` - 恢复日志目录
- ✓ `logs/verify/` - 验证日志目录

## 核心功能

### backup.sh
- 增量备份（rsync）
- 自动压缩（gzip，可配置级别）
- 时间戳命名（YYYYMMDD-HHMMSS）
- 本地和远程备份支持
- 自动清理旧备份（保留 N 个版本，默认 10）
- 完整日志记录
- 演练模式（--dry-run）

### restore.sh
- 列出所有可用备份
- 支持指定版本或最新版本恢复
- 恢复前完整性验证
- 自动创建恢复点
- 支持一键回滚
- 交互式确认机制

### backup_verify.sh
- 验证压缩包完整性
- 检查文件数量和大小
- 对比元数据与实际内容
- 生成详细验证报告
- 支持批量验证

## 备份内容

脚本会自动备份以下关键数据：
- `data/predictions.jsonl`
- `data/paper_trades.jsonl`
- `data/ledger.jsonl`
- `data/weight_history.jsonl`
- `data/pick_snapshots.jsonl`
- `data/debate_results.jsonl`
- `data/llm_calls.jsonl`
- `reports/` 目录（所有报告）
- `config/` 目录（所有配置）

## 快速开始

### 1. 创建第一个备份
```bash
cd /Users/ivon/Documents/AI量化选股
./scripts/backup.sh
```

### 2. 查看备份列表
```bash
./scripts/restore.sh --list
```

### 3. 验证备份
```bash
./scripts/backup_verify.sh --all
```

## 设置自动备份

### 方法 1: 使用 Cron
```bash
# 编辑 crontab
crontab -e

# 添加每日备份任务（每天凌晨 2:00）
0 2 * * * cd /Users/ivon/Documents/AI量化选股 && bash scripts/backup.sh >> logs/backup/cron.log 2>&1
```

### 方法 2: 使用 LaunchAgent（macOS 推荐）
参考 `config/backup_cron.example` 中的详细配置说明。

## 错误处理规范

所有脚本都包含：
- 完整的错误检查（set -euo pipefail）
- 详细的日志记录（带时间戳）
- 友好的错误消息
- 适当的退出代码
- 参数验证

## 日志记录

所有操作都会记录到对应的日志目录：
- 备份日志: `logs/backup/backup-TIMESTAMP.log`
- 恢复日志: `logs/restore/restore-TIMESTAMP.log`
- 验证日志: `logs/verify/verify-TIMESTAMP.log`
- Cron 日志: `logs/backup/cron.log`, `logs/verify/cron.log`

## 验证清单

在投入使用前，建议执行以下验证：

- [ ] 测试备份: `./scripts/backup.sh --dry-run`
- [ ] 执行实际备份: `./scripts/backup.sh`
- [ ] 验证备份完整性: `./scripts/backup_verify.sh --all`
- [ ] 测试恢复（在测试环境）: `./scripts/restore.sh --latest --verify-only`
- [ ] 检查日志文件是否正常生成
- [ ] 配置自动备份任务（cron 或 LaunchAgent）
- [ ] 设置远程备份（如需要）

## 最佳实践建议

1. **备份频率**: 
   - 生产环境: 每 4-6 小时
   - 开发环境: 每天一次
   
2. **验证频率**: 
   - 每天验证最新备份
   - 每周验证所有备份

3. **保留策略**: 
   - 本地: 保留最近 10 个版本
   - 远程: 保留最近 30 个版本

4. **3-2-1 原则**:
   - 3 份数据副本
   - 2 种不同存储介质
   - 1 份异地备份

5. **定期演练**: 
   - 每月进行一次完整的恢复演练
   - 确保团队熟悉恢复流程

## 支持文档

- 完整文档: `docs/BACKUP_SYSTEM.md`
- 快速参考: `docs/BACKUP_QUICK_REFERENCE.md`
- Cron 配置: `config/backup_cron.example`

## 故障排除

如遇到问题：
1. 检查脚本执行权限: `ls -lh scripts/backup*.sh scripts/restore.sh`
2. 查看日志文件: `tail -50 logs/backup/backup-*.log`
3. 验证磁盘空间: `df -h backups/`
4. 参考 `docs/BACKUP_SYSTEM.md` 中的故障排除章节

## 下一步

1. 阅读完整文档了解所有功能
2. 执行第一次备份并验证
3. 配置自动备份计划
4. 在测试环境中练习恢复流程
5. 设置远程备份（如需要）

---

**安装日期**: 2026-09-28  
**版本**: 1.0.0  
**项目**: AI量化选股  
**状态**: ✓ 安装完成，可以使用
