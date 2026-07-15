# Walk-Forward 证据边界记录（2026-07-13）

## 审计事实

- `data/walkforward_gate.json` 的 `data_end` 为 `2026-06-29`。
- 同一 sidecar 的 `production_gate_coverage.last_trade_date` 为 `20260529`。
- `data/walkforward_production_symbols.json` 曾记录 pytest 临时目录下的 `raw.db`，不能作为生产符号缓存来源。
- 当前 Mac 不提供 `/proc/meminfo`，只读取该文件会把内存检测变成 fail-open。

## 守卫结论

`scripts/run_production_walkforward_gate.py` 现在在启动子进程前执行以下 fail-closed 校验：

- raw SQLite `MAX(trade_date)` 必须存在；请求截止日、sidecar `data_end`、覆盖摘要截止日不得越过它，sidecar 截止日还必须等于本次请求截止日。
- 符号缓存绑定规范化的缓存路径、数据库路径、数据库 mtime 和 raw 截止日；路径或截止日不一致时丢弃缓存并重新扫描，不复用历史 pytest 产物。
- 内存检测依次支持 `/proc/meminfo`、POSIX `sysconf`（含 macOS）和 Windows API；所有检测方式都不可用时明确阻塞，不启动正式任务。

DSR/PBO 的计算和通过条件未修改。当前证据仍是双门失败，不得因资源或截止日守卫而改写为通过。

## 验证边界

已运行：

```text
python3 -m pytest -q tests/test_production_walkforward_gate.py tests/test_walkforward_gate.py
61 passed
ruff check scripts/run_production_walkforward_gate.py tests/test_production_walkforward_gate.py tests/test_walkforward_gate.py
All checks passed!
```

本次未启动正式高内存 walk-forward。
