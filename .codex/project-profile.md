# Project Profile

## Summary

AI 量化选股项目，目标是本地 Mac 为主运行的 A 股短线/超短线候选池、信号台账、虚拟盘、自我校验和通知系统。系统只辅助人工决策，不自动下单。

## Stack

Python 3.10+，pandas/numpy/scipy，DataSource 抽象，多源行情，jsonl ledger，Markdown/CSV/static HTML dashboard，pytest + ruff。

## Default Squad

Primary: Quant Trading Systems Engineer.

Support: Product/Notification Systems Engineer, Data Reliability Engineer, Paper-Trading/Execution Engineer, Code Reviewer.

## Current Goal

上线前运行正确性硬化：服务器必须以全市场、raw sqlite、真实冷启动样本、正式 walk-forward 证据、稳定通知去重和可复现 readiness gate 作为放行条件。当前重点是补齐 signal/paper 样本口径、修复 monitor/gate 通知防刷屏、同步服务器运行态，并继续压实策略/风控/阈值的一致性。

当前额外硬约束：`stable` walkforward grid 只允许使用已在历史证据中通过 DSR 的已验证子集；`exploratory` 才保留完整研究网格。生产 gate 失败时必须稳定产出非占位诊断报告，不能只依赖 formal report 是否完整写出。

## Constraints

不自动下单；不上传 `private_data/`、本地数据库、API key；secrets 只走 `.env` 或 GitHub Secrets；回测/虚拟盘使用不复权价格和真实 next open；`avoid` 不进入虚拟买入；`not_executable` 不进入胜率；禁止裸 `datetime.now()`；LLM/AI 只作解释和候选研究，不覆盖确定性评分。

遇到问题默认在项目内修复：代码、测试、脚本、文档、CI、可复现配置和展示文案优先。服务器系统配置、BT/Nginx/systemd/SSH/防火墙只读诊断，除非是安全、可用性或数据完整性级别的大问题，否则不直接修改，先交给仓主判断。

## Routing Cues

数据源/新鲜度问题交给 Data Reliability lens；策略/开源吸收交给 Research Librarian lens；虚拟盘/ledger/可成交性交给 Paper-Trading lens；通知/报告/多 Agent 摘要交给 Product/Notification lens；前端展示交给 Dashboard lens；合并前必须跑相关 pytest、ruff、脚本语法检查。
静态导出链路（`scripts/render_dashboard.py`、`scripts/render_agent_dashboard.py`、旧 `reports/`、研究发现 CLI）也属于用户可见面，不能只修 Streamlit 主面板而放任旧口径残留；所有时间戳继续强制带上海时区偏移。

如果再次出现“修了很多细节但上线阻塞还在”的情况，优先检查：1) 全市场 walkforward formal/diagnostic 报告链是否完整；2) gate sidecar 是否含有效 `grid_diagnostics`；3) 服务器定时入口是否仍有高频通知或样本口径漂移；不要回到文案层空转。

## Squad History

- 2026-05-29: Rerouted from prototype stock picker to local-first paper-trading system after user clarified the real objective and requested multi-agent execution.
- 2026-06-04: Added unified notification-template substrate for daily run, briefing, monitor, morning breakout, closing premium, and closing review so future agent work lands on one reusable output layer.
- 2026-06-26: Rerouted to launch-readiness reliability. Active blockers are real sample fill, formal production walk-forward, paper tracking sync, and notification spam guards; avoid cosmetic-only edits until these are green.
- 2026-06-27: Stable/exploratory walkforward profiles were re-separated. Production diagnostic reports must be refreshed from `walkforward_gate.json` even when an older diagnostic file already exists.
