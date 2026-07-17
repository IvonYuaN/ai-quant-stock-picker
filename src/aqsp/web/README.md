# AQSP Web

`src/aqsp/web/dashboard.py` 现在不是单纯的“主链只读看板”，而是一个 **按日期与任务回看的研究工作台**。

它只读取已经落盘的真实结果：

- `data/ledger.jsonl`
- `data/paper_trades.jsonl`
- `logs/trades/*.jsonl`
- `reports/*.md`

它不会连接券商，也不会伪造实时账户资产、当日盈亏或策略胜率。

## 当前能力

看板当前围绕“按日期回看当天多个定时任务”设计，核心区域包括：

- 顶部日期导航与同日阶段切换
- 决策首页驾驶舱
- 今日决策流
- 候选深度复盘
- 当日候选路径
- 执行工作台
- 同日归档中心

它适合回答这类问题：

- 某个交易日到底先看哪条任务链
- 同一只票当天在 `主链 / 早盘 / 尾盘` 里怎么变化
- 研究判断有没有进入虚拟盘执行
- 某天的归档摘要、明日重点、运行快照分别是什么

## 历史回滚入口

```bash
bash scripts/start_dashboard.sh
```

或：

```bash
streamlit run src/aqsp/web/dashboard.py --server.port 8501
```

该入口只用于正式 React + FastAPI 入口故障时的临时回滚；当前生产入口请使用
`bash scripts/start_vibe_research.sh`，访问 `http://127.0.0.1:5899`。

如果没有主链产物，页面会显示空态提示，这是预期行为。

## 历史回滚部署

如需临时回滚，公网部署默认不要直接暴露 Streamlit 端口，而是：

- Streamlit 监听 `127.0.0.1:8501`
- 域名指向 Nginx / Caddy
- 反向代理到 `127.0.0.1:8501`
- 开 HTTPS
- 加 Basic Auth 或统一登录

具体见 `/Users/ivon/Documents/AI量化选股/docs/DASHBOARD_GUIDE.md`。
