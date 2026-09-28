# 备份系统快速参考

## 常用命令

### 备份
```bash
# 立即备份
./scripts/backup.sh

# 远程备份
./scripts/backup.sh --remote user@server:/backup/path

# 演练模式
./scripts/backup.sh --dry-run
```

### 恢复
```bash
# 列出备份
./scripts/restore.sh --list

# 恢复最新
./scripts/restore.sh --latest

# 恢复指定备份
./scripts/restore.sh --backup backup-20260928-120000

# 回滚
./scripts/restore.sh --rollback
```

### 验证
```bash
# 验证单个
./scripts/backup_verify.sh --backup backup-20260928-120000.tar.gz

# 验证所有
./scripts/backup_verify.sh --all

# 生成报告
./scripts/backup_verify.sh --all --report reports/verify.txt
```

## 自动化

### Cron (每日备份 2:00)
```bash
0 2 * * * cd /Users/ivon/Documents/AI量化选股 && bash scripts/backup.sh >> logs/backup/cron.log 2>&1
```

### 手动测试
```bash
# 测试备份
./scripts/backup.sh --dry-run

# 测试验证
./scripts/backup_verify.sh --all --quiet
```

## 紧急恢复流程

1. 列出可用备份: `./scripts/restore.sh --list`
2. 验证备份: `./scripts/backup_verify.sh --backup FILENAME`
3. 执行恢复: `./scripts/restore.sh --backup FILENAME`
4. 如有问题: `./scripts/restore.sh --rollback`

## 文件位置

- 脚本: `scripts/backup.sh`, `scripts/restore.sh`, `scripts/backup_verify.sh`
- 备份: `backups/backup-*.tar.gz`
- 日志: `logs/backup/`, `logs/restore/`, `logs/verify/`
- 配置: `config/backup_cron.example`
- 文档: `docs/BACKUP_SYSTEM.md`
