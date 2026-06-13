# Tests Index

测试目录按“主链路能力”分组理解，不按新增时间堆叠。

## Daily Run / CLI

- `test_cli_no_qfq.py`
- `test_cli_notify.py`
- `test_cli_walkforward.py`
- `test_diagnose_runtime.py`
- `test_integration.py`

## Data Sources / Freshness

- `test_data_cache.py`
- `test_data_freshness.py`
- `test_data_intraday.py`
- `test_data_multi.py`
- `test_data_registry.py`
- `test_data_source.py`
- `test_download_tdx_vipdoc.py`
- `test_freshness.py`
- `test_index_constituents.py`
- `test_pit_financial.py`
- `test_tdx_vipdoc_source.py`
- `test_tencent_source.py`

## Strategies / Backtest

- `test_backtest.py`
- `test_executable_uses_real_limits.py`
- `test_heldout_guard.py`
- `test_mean_reversion.py`
- `test_notification_gate.py`
- `test_strategies.py`
- `test_strategy.py`
- `test_triple_rise.py`
- `test_volume_strategy.py`

## Universe / Risk / Ledger / Paper

- `test_filters_lethal.py`
- `test_ledger.py`
- `test_ledger_e2e.py`
- `test_margin_trading.py`
- `test_northbound.py`
- `test_paper.py`
- `test_t1_filter.py`
- `test_universe.py`

## Reporting / Briefing / Dashboard / Monitor

- `test_briefing.py`
- `test_briefing_e2e.py`
- `test_dashboard.py`
- `test_open_dashboard.py`
- `test_email_notifier.py`
- `test_monitor.py`
- `test_report.py`

## Guardrails / Hygiene

- `test_check_no_secrets.py`
- `test_core_errors.py`
- `test_core_time.py`
- `test_diagnose_momentum.py`
- `test_preflight_upload.py`
- `test_runtime_redline_guard.py`

全量回归：

```bash
python3 -m pytest -q
```

仅跑与发布最相关的主链路：

```bash
python3 -m pytest -q \
  tests/test_cli_walkforward.py \
  tests/test_backtest.py \
  tests/test_notification_gate.py \
  tests/test_dashboard.py
```
