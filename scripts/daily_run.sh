#!/usr/bin/env bash
# 每日收盘后执行：跑选股 -> 生成 briefing -> 日志
# 由 macOS launchd 在工作日 16:00 触发
set -e

PROJECT_ROOT="$HOME/Documents/AI量化选股"
cd "$PROJECT_ROOT"

DATE=$(date +%Y-%m-%d)
LOG_DIR="$PROJECT_ROOT/logs/daily"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run-$DATE.log"

DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
    echo "[$(date)] 周末跳过" >> "$LOG"
    exit 0
fi

export PATH="$HOME/Library/Python/3.11/bin:$PATH"

{
    echo "=== aqsp run @ $(date) ==="
    python3 -m aqsp run --source akshare 2>&1 || echo "aqsp run failed: $?"

    echo ""
    echo "=== aqsp briefing @ $(date) ==="
    python3 -m aqsp briefing --output "reports/briefing-$DATE.md" 2>&1 || echo "briefing failed: $?"

    echo ""
    echo "=== ledger 当前行数 ==="
    wc -l data/predictions.jsonl 2>/dev/null || echo "ledger not found"
} >> "$LOG" 2>&1

echo "[$(date)] daily_run done, log: $LOG"
