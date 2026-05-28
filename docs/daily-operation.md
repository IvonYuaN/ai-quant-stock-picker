# 每日运行手册（Phase 1 半实盘）

## 自动跑（推荐）

加载 launchd 任务（一次性）：
```bash
cp scripts/launchd/com.aqsp.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aqsp.daily.plist
launchctl list | grep com.aqsp
```

## 手动跑（验证用）

```bash
bash scripts/daily_run.sh
tail -50 logs/daily/run-$(date +%Y-%m-%d).log
```

## 卸载

```bash
launchctl unload ~/Library/LaunchAgents/com.aqsp.daily.plist
```

## 检查任务状态

```bash
launchctl list | grep com.aqsp
ls -la /tmp/aqsp-daily.{out,err}
```

## 30 天冷启动追踪

冷启动需要 30 个独立信号日（CONSTITUTION §1.3 #14）。
每日跑结束后，检查：
```bash
wc -l data/predictions.jsonl
```

每个独立交易日至少写入一行才算数。
