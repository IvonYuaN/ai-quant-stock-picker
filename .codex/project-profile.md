# Project Profile

## Summary

AI 量化选股项目，目标是本地 Mac 为主运行的 A 股短线/超短线候选池、信号台账、虚拟盘、自我校验和通知系统。系统只辅助人工决策，不自动下单。

## Stack

Python 3.10+，pandas/numpy/scipy，DataSource 抽象，多源行情，jsonl ledger，Markdown/CSV/static HTML dashboard，pytest + ruff。

## Default Squad

Primary: Quant Trading Systems Engineer.

Support: Product/Notification Systems Engineer, Data Reliability Engineer, Paper-Trading/Execution Engineer, Code Reviewer.

## Current Goal

打通并收敛一条稳定的本地/服务器日常主线：`daily_run.sh -> aqsp run -> ledger -> aqsp paper -> aqsp briefing -> aqsp dashboard -> notification/logs`。当前重点是把主链、复盘、监控、早盘、尾盘通知统一到可复用模板层，并保留多 Agent 辩论摘要与数据源状态。

## Constraints

不自动下单；不上传 `private_data/`、本地数据库、API key；secrets 只走 `.env` 或 GitHub Secrets；回测/虚拟盘使用不复权价格和真实 next open；`avoid` 不进入虚拟买入；`not_executable` 不进入胜率；禁止裸 `datetime.now()`；LLM/AI 只作解释和候选研究，不覆盖确定性评分。

遇到问题默认在项目内修复：代码、测试、脚本、文档、CI、可复现配置和展示文案优先。服务器系统配置、BT/Nginx/systemd/SSH/防火墙只读诊断，除非是安全、可用性或数据完整性级别的大问题，否则不直接修改，先交给仓主判断。

## Routing Cues

数据源/新鲜度问题交给 Data Reliability lens；策略/开源吸收交给 Research Librarian lens；虚拟盘/ledger/可成交性交给 Paper-Trading lens；通知/报告/多 Agent 摘要交给 Product/Notification lens；前端展示交给 Dashboard lens；合并前必须跑相关 pytest、ruff、脚本语法检查。

## Squad History

- 2026-05-29: Rerouted from prototype stock picker to local-first paper-trading system after user clarified the real objective and requested multi-agent execution.
- 2026-06-04: Added unified notification-template substrate for daily run, briefing, monitor, morning breakout, closing premium, and closing review so future agent work lands on one reusable output layer.
