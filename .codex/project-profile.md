# Project Profile

## Summary

AI 量化选股项目，目标是本地 Mac 为主运行的 A 股短线/超短线候选池、信号台账、虚拟盘、自我校验和通知系统。系统只辅助人工决策，不自动下单。

## Stack

Python 3.10+，pandas/numpy/scipy，DataSource 抽象，多源行情，jsonl ledger，Markdown/CSV/static HTML dashboard，pytest + ruff。

## Default Squad

Primary: Quant Trading Systems Engineer.

Support: Product/Notification Systems Engineer, Data Reliability Engineer, Paper-Trading/Execution Engineer, Code Reviewer.

## Current Goal

打通并收敛一条稳定的本地/服务器日常主线：`daily_run.sh -> aqsp run -> ledger -> aqsp paper -> aqsp briefing -> aqsp dashboard -> notification/logs`。当前重点是持续推进可测试、可评审的最小开发任务；主线程负责集成和最终验证，默认用多 agent 做只读侦察、独立审查或互不重叠的代码切片。

## Constraints

不自动下单；不上传 `private_data/`、本地数据库、API key；secrets 只走 `.env` 或 GitHub Secrets；回测/虚拟盘使用不复权价格和真实 next open；`avoid` 不进入虚拟买入；`not_executable` 不进入胜率；禁止裸 `datetime.now()`；LLM/AI 只作解释和候选研究，不覆盖确定性评分。

遇到问题默认在项目内修复：代码、测试、脚本、文档、CI、可复现配置和展示文案优先。服务器系统配置、BT/Nginx/systemd/SSH/防火墙只读诊断，除非是安全、可用性或数据完整性级别的大问题，否则不直接修改，先交给仓主判断。

## Routing Cues

数据源/新鲜度问题交给 Data Reliability lens；策略/开源吸收交给 Research Librarian lens；虚拟盘/ledger/可成交性交给 Paper-Trading lens；通知/报告/多 Agent 摘要交给 Product/Notification lens；前端展示交给 Dashboard lens；合并前必须跑相关 pytest、ruff、脚本语法检查。
静态导出链路（`scripts/render_dashboard.py`、`scripts/render_agent_dashboard.py`、旧 `reports/`、研究发现 CLI）也属于用户可见面，不能只修 Streamlit 主面板而放任旧口径残留；所有时间戳继续强制带上海时区偏移。
默认 squad：Data Reliability Engineer + Minimal Change Engineer + Code Reviewer。对小型修复，主线程实现，子 agent 并行做独立审查或下一任务侦察；对多文件中型改动，拆成互不重叠的 worker 写集。

## Squad History

- 2026-05-29: Rerouted from prototype stock picker to local-first paper-trading system after user clarified the real objective and requested multi-agent execution.
- 2026-06-04: Added unified notification-template substrate for daily run, briefing, monitor, morning breakout, closing premium, and closing review so future agent work lands on one reusable output layer.
- 2026-06-13: User requested automatic goal tracking and multi-agent development. Active goal created for continuous minimal, testable AQSP development with main-thread integration and sub-agent review/scouting.
