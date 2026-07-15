# Raw SQLite 截止日审计（2026-07-13）

## 结论

本次只审计历史验证数据链路，没有启动 walk-forward，也没有修改 DSR/PBO 条件或 live_short 数据源。服务器当前没有复现 `sidecar.data_end > raw MAX(trade_date)`：

- sidecar：`/opt/aqsp/data/walkforward_gate.json`，`data_end=2024-12-31`，`sqlite_db_path=/opt/market-data/astocks_raw.db`。
- raw SQLite：`MAX(trade_date)=20260707`，有效范围 `20180102..20260707`。
- 因此当前 sidecar 是旧于 raw 尾日，而不是超过 raw 尾日；现有 production gate 的 cutoff guard 应保持 fail-closed。

## 服务器证据

生产环境通过 `/opt/aqsp/scripts/bt_task.sh coldstart` 调用仓库内
`/opt/aqsp/scripts/update_sqlite_daily.py`，目标库为
`/opt/market-data/astocks_raw.db`。raw 表 schema 为 `daily_qfq(ts_code,
trade_date, open, high, low, close_qfq, volume, amount, open_qfq, high_qfq,
low_qfq, close)`，并有 `UNIQUE(ts_code, trade_date)`。

服务器只读查询结果：

```text
MIN(trade_date)=20180102
MAX(trade_date)=20260707
COUNT(*)=9104765
COUNT(DISTINCT ts_code)=5441
20260707|696
20260706|5203
20260703|5204
20260702|5206
20260701|5205
```

2026-07-07 的 coldstart 日志显示 Baostock 连接中断后，目标日覆盖为
`696/3000` 并退出；2026-07-08 起因为冷启动样本门已达标，任务直接跳过
SQLite 更新。因此 raw 尾日冻结在 2026-07-07 是任务行为和上游断流共同造成的，
不是 sidecar 自动推进造成的。

## 修复与阻塞行为

`scripts/update_sqlite_daily.py` 现在在写入后重新查询真实 `MAX(trade_date)`：

- 默认 `price_mode=raw`，qfq 只能显式指定，避免验证库被误写成 qfq。
- 目标标的非空但目标日没有任何行，返回非零并打印 fail-closed 诊断。
- 实际 `MAX(trade_date)` 早于请求目标时，返回非零并打印 cutoff 诊断；请求目标不会被当作已覆盖截止日。
- 更新摘要同时记录 `raw_max_trade_date` 和 `coverage_error`，供任务日志审计。

## 可执行补数命令

只在目标交易日收盘后执行；下面命令不会启动 walk-forward，也不会把 SQLite 接入
`live_short`：

```bash
cd /opt/aqsp
flock -n /tmp/aqsp-raw-backfill.lock \
  /opt/aqsp/.venv/bin/python3 -u scripts/update_sqlite_daily.py \
  /opt/market-data/astocks_raw.db \
  --target-date 2026-07-10 \
  --price-mode raw \
  --sleep-seconds 0.05 \
  --query-timeout-seconds 15
```

上例使用 2026-07-10 作为 2026-07-13 收盘前最后一个已完成交易日。收盘后应将
`--target-date` 改为实际已完成交易日；若需修复历史前缀缺口，再追加：

```bash
--start-date 2018-01-01 --fill-history-gaps
```

若 Baostock 再次断流，保持同一命令重跑即可按每标的最新日期续补；不要手工写入
`trade_date`、sidecar 或任何 DSR/PBO 字段。补数完成后先核对 raw `MAX(trade_date)`
和目标日覆盖，再由既有 production gate 自行决定是否继续，不能用旧 sidecar 代替
新证据。
