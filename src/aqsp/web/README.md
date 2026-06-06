# AQSP Web

`src/aqsp/web/dashboard.py` 是一个 **主链只读看板**。

它只展示已经落盘的运行数据：

- `data/ledger.jsonl`
- `data/paper_trades.jsonl`
- `logs/trades/*.jsonl`

它不会连接券商，也不会伪造实时账户资产、当日盈亏或策略胜率。

启动：

```bash
bash scripts/start_dashboard.sh
```

或：

```bash
streamlit run src/aqsp/web/dashboard.py --server.port 8501
```

如果没有主链产物，页面会显示空态提示，这是预期行为。
