# 新手看板说明

`src/aqsp/web/dashboard_beginner.py` 是面向新手的 Streamlit 看板。它不再使用示例账户或示例持仓，只读取现有 `DashboardDataProvider` 的真实落盘数据。

## 数据来源

- `data/predictions.jsonl`：主链、早盘、盘中、尾盘、复盘等任务的研究结果。
- `data/paper_trades.jsonl`：纸面入场、纸面持有、不可成交、关闭记录。
- `reports/`：简报和复盘归档。
- `logs/trades/`：交易日志和纸面回写事件。

看板没有接入券商账户，所以不会展示“真实总资产”“真实现金”“真实今日盈亏”。它只展示系统已经落盘并可审计的纸面状态。

## 顶部导航

顶部固定按一天的使用节奏展示：

| 时间 | 名称 | 数据任务 |
|---|---|---|
| 09:25 | 开盘前 | `main_chain` |
| 10:00 | 早盘看一眼 | `morning_breakout` |
| 12:00 | 午盘回看 | `intraday` |
| 14:40 | 尾盘确认 | `closing_premium` |
| 15:30 | 收盘复盘 | `closing_review` |
| 21:00 | 明日预案 | `briefing` |

如果某个时间点当天没有独立落盘结果，下拉项会标记“暂无”，页面不会用假数据补齐。

## 运行

```bash
python3 -m streamlit run src/aqsp/web/dashboard_beginner.py --server.address 127.0.0.1 --server.port 8502
```

服务器生产看板仍建议使用宝塔反向代理到 Streamlit 服务；调试时不要使用用户前台浏览器，优先使用 `scripts/headless_dashboard_check.py` 或 `curl`。

## 表达原则

- 只说系统知道的事实，不推断真实账户。
- 用“纸面持有”“纸面入场”“观察”“卡点”“不可成交”等词，避免让人误以为系统在下单。
- 先给结论和卡点，再给候选表。
- 午盘只是回看上午变化，不污染正式收盘 ledger。
