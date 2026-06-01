# Project Profile

## Summary

AI 量化选股项目，目标是本地 Mac 为主运行的 A 股短线/超短线候选池、信号台账、虚拟盘、自我校验和通知系统。系统只辅助人工决策，不自动下单。

## Stack

Python 3.10+，pandas/numpy/scipy，DataSource 抽象，多源行情，jsonl ledger，Markdown/CSV/static HTML dashboard，pytest + ruff。

## Default Squad

Primary: Quant Trading Systems Engineer.

Support: Data Reliability Engineer, Paper-Trading/Execution Engineer, Research Librarian, Code Reviewer.

## Current Goal

打通一条本地日常主线：`daily_run.sh -> aqsp run -> ledger -> aqsp paper -> aqsp briefing -> aqsp dashboard -> notification/logs`。默认源计划为 `auto`，本地 TDX vipdoc 优先，东方财富/新浪/腾讯/AKShare 兜底；没有本地 2G 文件时也必须有明确 fallback 或显式失败原因。

## Constraints

不自动下单；不上传 `private_data/`、本地数据库、API key；secrets 只走 `.env` 或 GitHub Secrets；回测/虚拟盘使用不复权价格和真实 next open；`avoid` 不进入虚拟买入；`not_executable` 不进入胜率；禁止裸 `datetime.now()`；LLM/AI 只作解释和候选研究，不覆盖确定性评分。

## Routing Cues

数据源/新鲜度问题交给 Data Reliability lens；策略/开源吸收交给 Research Librarian lens；虚拟盘/ledger/可成交性交给 Paper-Trading lens；前端展示交给 Dashboard lens；合并前必须跑相关 pytest、ruff、脚本语法检查。

## Squad History

- 2026-05-29: Rerouted from prototype stock picker to local-first paper-trading system after user clarified the real objective and requested multi-agent execution.
