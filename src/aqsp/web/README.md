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

如果已经有备案域名，公网部署默认不要直接暴露 Streamlit 端口，而是：

- Streamlit 监听 `127.0.0.1:8501`
- 域名指向 Nginx / Caddy
- 反向代理到 `127.0.0.1:8501`
- 开 HTTPS
- 加 Basic Auth 或统一登录

具体见 `/Users/ivon/Documents/AI量化选股/docs/DASHBOARD_GUIDE.md`。
