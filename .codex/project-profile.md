# Project Profile

## Summary

AI 量化选股项目，目标是本地 Mac 为主运行的 A 股短线/超短线候选池、信号台账、虚拟盘、自我校验和通知系统。系统只辅助人工决策，不自动下单。

## Stack

Python 3.10+，pandas/numpy/scipy，DataSource 抽象，多源行情，jsonl ledger，Markdown/CSV/static HTML dashboard，pytest + ruff。

## Default Squad

Primary: Short-Term Quant Decision Systems Engineer.

Support: Real-Time Data Reliability Engineer, Market Intelligence/News Fusion Engineer, Multi-Agent Decision Systems Engineer, Paper-Trading/Execution Engineer, Code Reviewer.

## Current Goal

将 AQSP 演进为面向短线决策研究的实时多源数据与多代理研判系统。实时数据优先于历史数据，历史数据只用于回测、walk-forward 验证和阈值冻结证据；盘中链路优先保证新鲜度、可成交性和信息时效。策略默认围绕短线/超短线机会检测，允许受控自动优化，但优化结果只能进入阈值与权重层，不能直接覆盖确定性评分与人工决策边界。

当前主线分四条并行推进：1) 压实实时行情/分时/quote 新鲜度与降级链路；2) 明确历史数据只进回测与验证、不反向污染盘中决策；3) 把国内外高价值信息源作为决策上下文接入并排序；4) 收敛多 Agent 讨论、投票和意见汇总，使其成为候选建议增强层而非下单层。

当前执行地图固定在 `docs/short-term-realtime-roadmap.md`，默认开关矩阵固定在 `config/goal_switches.yaml`。后续整理、开发、评审与续跑都以这两个文件为控偏入口。

## Constraints

不自动下单；不上传 `private_data/`、本地数据库、API key；secrets 只走 `.env` 或 GitHub Secrets；回测/虚拟盘使用不复权价格和真实 next open；`avoid` 不进入虚拟买入；`not_executable` 不进入胜率；禁止裸 `datetime.now()`；LLM/AI 只作解释、信息整理、辩论与候选研究，不覆盖确定性评分。

禁用技能调用：`superpower`、`superpowers`、`using-superpowers` 及其路由别名。本项目后续任务不得调用、路由或自动启用这些技能；仅使用项目已允许的规则、工具和技能。

历史行情、历史新闻、历史回测统计只允许用于离线验证、walk-forward、阈值冻结与 agent 评估，不允许在盘中以未来视角反向污染实时建议。多 Agent 可以讨论、质疑、加减权，但最终产出仍是“研究建议/纸面决策支持”，不是交易指令。

遇到问题默认在项目内修复：代码、测试、脚本、文档、CI、可复现配置和展示文案优先。服务器系统配置、BT/Nginx/systemd/SSH/防火墙只读诊断，除非是安全、可用性或数据完整性级别的大问题，否则不直接修改，先交给仓主判断。

## Routing Cues

实时行情、分时补齐、quote 新鲜度、fallback 降级交给 Real-Time Data Reliability lens；策略/开源吸收/自动优化边界交给 Quant Research lens；国内外新闻、指数、宏观、政策、资金流和情绪输入交给 Market Intelligence lens；多 Agent 辩论、角色编排、投票汇总和解释文案交给 Multi-Agent Decision lens；虚拟盘/ledger/可成交性交给 Paper-Trading lens；前端展示交给 Dashboard lens；合并前必须跑相关 pytest、ruff、脚本语法检查。
静态导出链路（`scripts/render_dashboard.py`、`scripts/render_agent_dashboard.py`、旧 `reports/`、研究发现 CLI）也属于用户可见面，不能只修 Streamlit 主面板而放任旧口径残留；所有时间戳继续强制带上海时区偏移。任何“优化”若涉及盘中建议，必须先回答它依赖的是实时数据还是历史证据，并验证没有把历史回测口径误接到实时链路。

如果再次出现“修了很多细节但上线阻塞还在”的情况，优先检查：1) 全市场 walkforward formal/diagnostic 报告链是否完整；2) gate sidecar 是否含有效 `grid_diagnostics`；3) 服务器定时入口是否仍有高频通知或样本口径漂移；不要回到文案层空转。
生产全市场 walk-forward 不得在低内存小服务器上白天直接启动；2026-07-09 实测会拖死 SSH/公网看板。默认依赖 `scripts/run_production_walkforward_gate.py` 的低内存 guard，需更大机器或显式 `AQSP_ALLOW_LOW_MEMORY_WALKFORWARD=1` 才能运行正式子进程。

## Squad History

- 2026-05-29: Rerouted from prototype stock picker to local-first paper-trading system after user clarified the real objective and requested multi-agent execution.
- 2026-06-04: Added unified notification-template substrate for daily run, briefing, monitor, morning breakout, closing premium, and closing review so future agent work lands on one reusable output layer.
- 2026-06-26: Rerouted to launch-readiness reliability. Active blockers are real sample fill, formal production walk-forward, paper tracking sync, and notification spam guards; avoid cosmetic-only edits until these are green.
- 2026-06-27: Stable/exploratory walkforward profiles were re-separated. Production diagnostic reports must be refreshed from `walkforward_gate.json` even when an older diagnostic file already exists.
- 2026-06-30: Rerouted to short-term realtime decision support. Active priorities are realtime freshness, historical-vs-runtime boundary enforcement, domestic/global market intelligence fusion, and multi-agent discussion as an advisory layer.
- 2026-07-09: Production walk-forward follow-up made visible on homepage; low-memory server launch was found unsafe and should be blocked by guardrail rather than repeated manually.
- 2026-07-10: Intraday route hardened after stale/slow homepage incident. Realtime candidate artifacts must be written as soon as candidates are screened; full diagnostics, portfolio discussion, and multi-agent research may enrich later but must not block the live dashboard.
