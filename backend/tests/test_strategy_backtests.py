from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
import strategy_backtests
import strategy_definitions
import tenant


class ReplaySource:
    name = "sqlite_db"

    def __init__(self, *, latest: date = date(2026, 10, 30)):
        self.latest = latest

    def price_mode(self):
        return "raw"

    def get_latest_daily_date(self):
        return date(2026, 10, 30)

    def get_available_daily_dates(self, start, end):
        dates = []
        current = start
        from datetime import timedelta
        while current <= end:
            if current.weekday() < 5:
                dates.append(current)
            current += timedelta(days=1)
        return dates

    def get_latest_daily_dates(self, symbols, *, as_of=None):
        return {symbol: self.latest for symbol in symbols}

    def get_symbols_with_daily_bars(self, start, end):
        return ["600002", "600001"]


@pytest.fixture
def replay_store(monkeypatch, tmp_path):
    monkeypatch.setenv("VR_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(strategy_definitions.pf, "CACHE_DIR", str(tmp_path / "definitions"))
    token = tenant.current_tenant.set("local")
    yield tmp_path
    tenant.current_tenant.reset(token)


def _save_strategy():
    return strategy_definitions.save_strategy(
        strategy_id="entry_exit_demo",
        name="入场退出回放",
        description="测试日线回放任务",
        expression="close > ma20",
        exit_expression="close < ma20",
        enabled=True,
        expected_revision=None,
    )


def test_strategy_backtest_start_freezes_snapshot_and_rejects_stale_symbol(
    replay_store, monkeypatch
):
    strategy = _save_strategy()
    source = ReplaySource()
    monkeypatch.setattr(strategy_backtests, "build_data_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        strategy_backtests.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=987654),
    )
    run = strategy_backtests.start_run(
        strategy_id=strategy["id"],
        symbols=["600001"],
        signal_start="2026-10-26",
        signal_end="2026-10-28",
        outcome_as_of="2026-10-30",
    )
    config = strategy_backtests._read(
        strategy_backtests._run_dir(run["run_id"]) / "config.json"
    )
    assert run["status"] == "running"
    assert config["strategy"]["revision"] == 1
    assert config["strategy"]["exit_expression"] == "close < ma20"
    assert config["policy"]["thresholds_version"]
    assert config["policy"]["fill_rule"] == "signal_close_next_open"

    monkeypatch.setattr(
        strategy_backtests,
        "build_data_source",
        lambda *_args, **_kwargs: ReplaySource(latest=date(2026, 10, 29)),
    )
    with pytest.raises(strategy_backtests.StrategyBacktestError, match="2026-10-29"):
        strategy_backtests.start_run(
            strategy_id=strategy["id"],
            symbols=["600001"],
            signal_start="2026-10-26",
            signal_end="2026-10-28",
            outcome_as_of="2026-10-30",
        )


def test_strategy_backtest_tenant_scope_and_api_reject_composite_gate_mixing(
    replay_store, monkeypatch
):
    monkeypatch.setattr(strategy_backtests, "build_data_source", lambda *_args, **_kwargs: ReplaySource())
    monkeypatch.setattr(
        strategy_backtests.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=987655),
    )
    client = TestClient(app_module.app)
    owner = {"X-User-Id": "strategy-replay-owner"}
    other = {"X-User-Id": "strategy-replay-other"}
    strategy_response = client.post(
        "/api/aqsp/strategy-definitions",
        headers=owner,
        json={
            "id": "entry_exit_demo",
            "name": "入场退出回放",
            "description": "测试日线回放任务",
            "expression": "close > ma20",
            "exit_expression": "close < ma20",
            "enabled": True,
        },
    )
    assert strategy_response.status_code == 200
    response = client.post(
        "/api/aqsp/strategy-backtests",
        headers=owner,
        json={
            "strategy_id": "entry_exit_demo",
            "symbol": "600001",
            "signal_start": "2026-10-26",
            "signal_end": "2026-10-28",
            "outcome_as_of": "2026-10-30",
        },
    )
    assert response.status_code == 200, response.json()
    run_id = response.json()["data"]["run_id"]
    assert client.get(f"/api/aqsp/strategy-backtests/{run_id}", headers=other).status_code == 404
    assert "composite" not in response.json()["data"]


def test_strategy_backtest_portfolio_api_freezes_account_mode_and_symbol_set(
    replay_store, monkeypatch
):
    monkeypatch.setattr(strategy_backtests, "build_data_source", lambda *_args, **_kwargs: ReplaySource())
    monkeypatch.setattr(
        strategy_backtests.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=987656),
    )
    client = TestClient(app_module.app)
    headers = {"X-User-Id": "strategy-portfolio-owner"}
    created = client.post(
        "/api/aqsp/strategy-definitions",
        headers=headers,
        json={
            "id": "portfolio_demo",
            "name": "策略组合回放",
            "description": "测试共享现金组合契约",
            "expression": "close > ma20",
            "exit_expression": "close < ma20",
            "enabled": True,
        },
    )
    assert created.status_code == 200
    response = client.post(
        "/api/aqsp/strategy-backtests",
        headers=headers,
        json={
            "strategy_id": "portfolio_demo",
            "mode": "portfolio",
            "symbols": ["600002", "600001", "600001"],
            "initial_capital": 1_000_000,
            "signal_start": "2026-10-26",
            "signal_end": "2026-10-28",
            "outcome_as_of": "2026-10-30",
        },
    )

    assert response.status_code == 200, response.json()
    run = response.json()["data"]
    assert run["mode"] == "portfolio"
    assert run["symbols"] == ["600001", "600002"]
    assert run["initial_capital"] == 1_000_000
    assert "composite" not in run
    tenant_token = tenant.current_tenant.set(tenant.resolve_tenant_id(headers["X-User-Id"], ""))
    config = strategy_backtests._read(strategy_backtests._run_dir(run["run_id"]) / "config.json")
    tenant.current_tenant.reset(tenant_token)
    assert config["policy"]["max_positions"] == strategy_backtests._policy_snapshot()["max_positions"]
    assert config["policy"]["min_cash_reserve_pct"] == strategy_backtests._policy_snapshot()["min_cash_reserve_pct"]


def test_strategy_backtest_portfolio_requires_initial_capital(replay_store, monkeypatch):
    monkeypatch.setattr(strategy_backtests, "build_data_source", lambda *_args, **_kwargs: ReplaySource())
    client = TestClient(app_module.app)
    headers = {"X-User-Id": "portfolio-capital-required"}
    created = client.post(
        "/api/aqsp/strategy-definitions",
        headers=headers,
        json={
            "id": "portfolio_capital",
            "name": "组合资金验证",
            "description": "",
            "expression": "close > ma20",
            "enabled": True,
        },
    )
    assert created.status_code == 200
    response = client.post(
        "/api/aqsp/strategy-backtests",
        headers=headers,
        json={
            "strategy_id": "portfolio_capital",
            "mode": "portfolio",
            "symbols": ["600001"],
            "signal_start": "2026-10-26",
            "signal_end": "2026-10-28",
            "outcome_as_of": "2026-10-30",
        },
    )

    assert response.status_code == 422
    assert "必须设置正数初始资金" in response.json()["detail"]


def test_strategy_backtest_local_history_universe_freezes_symbols_and_source_window(
    replay_store, monkeypatch
):
    source = ReplaySource()
    monkeypatch.setattr(strategy_backtests, "build_data_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        strategy_backtests.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=987658),
    )
    strategy = _save_strategy()

    run = strategy_backtests.start_run(
        strategy_id=strategy["id"],
        symbols=None,
        signal_start="2026-10-26",
        signal_end="2026-10-28",
        outcome_as_of="2026-10-30",
        mode="portfolio",
        initial_capital=100_000,
        universe_mode="local_history",
    )
    config = strategy_backtests._read(
        strategy_backtests._run_dir(run["run_id"]) / "config.json"
    )

    assert run["universe_mode"] == "local_history"
    assert run["universe_as_of"] == "2026-10-28"
    assert run["universe_symbol_count"] == 2
    assert run["symbols"] == ["600001", "600002"]
    assert run["universe_hash"] == config["universe_hash"]
    assert config["symbols"] == ["600001", "600002"]


def test_strategy_backtest_api_accepts_local_history_universe(replay_store, monkeypatch):
    monkeypatch.setattr(strategy_backtests, "build_data_source", lambda *_args, **_kwargs: ReplaySource())
    monkeypatch.setattr(
        strategy_backtests.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=987659),
    )
    client = TestClient(app_module.app)
    headers = {"X-User-Id": "local-history-api-owner"}
    created = client.post(
        "/api/aqsp/strategy-definitions",
        headers=headers,
        json={
            "id": "history_universe",
            "name": "历史行情池策略",
            "expression": "close > 0",
            "enabled": True,
        },
    )
    assert created.status_code == 200
    response = client.post(
        "/api/aqsp/strategy-backtests",
        headers=headers,
        json={
            "strategy_id": "history_universe",
            "mode": "portfolio",
            "universe_mode": "local_history",
            "initial_capital": 100_000,
            "signal_start": "2026-10-26",
            "signal_end": "2026-10-28",
            "outcome_as_of": "2026-10-30",
        },
    )

    assert response.status_code == 200, response.json()
    assert response.json()["data"]["universe_mode"] == "local_history"
    assert response.json()["data"]["universe_symbol_count"] == 2


def test_strategy_backtest_freezes_score_expression_and_weighting_mode(replay_store, monkeypatch):
    monkeypatch.setattr(strategy_backtests, "build_data_source", lambda *_args, **_kwargs: ReplaySource())
    monkeypatch.setattr(
        strategy_backtests.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=987657),
    )
    client = TestClient(app_module.app)
    headers = {"X-User-Id": "score-weight-owner"}
    created = client.post(
        "/api/aqsp/strategy-definitions",
        headers=headers,
        json={
            "id": "scored_portfolio",
            "name": "截面评分组合",
            "description": "",
            "expression": "close > 10",
            "score_expression": "rank(volume_ratio) + rank(ret_5)",
            "enabled": True,
        },
    )
    assert created.status_code == 200
    response = client.post(
        "/api/aqsp/strategy-backtests",
        headers=headers,
        json={
            "strategy_id": "scored_portfolio",
            "mode": "portfolio",
            "position_sizing": "score_weight",
            "symbols": ["600001", "600002"],
            "initial_capital": 100_000,
            "signal_start": "2026-10-26",
            "signal_end": "2026-10-28",
            "outcome_as_of": "2026-10-30",
        },
    )

    assert response.status_code == 200, response.json()
    run = response.json()["data"]
    assert run["position_sizing"] == "score_weight"
    tenant_token = tenant.current_tenant.set(tenant.resolve_tenant_id(headers["X-User-Id"], ""))
    config = strategy_backtests._read(strategy_backtests._run_dir(run["run_id"]) / "config.json")
    tenant.current_tenant.reset(tenant_token)
    assert config["position_sizing"] == "score_weight"
    assert config["strategy"]["score_expression"] == "rank(volume_ratio) + rank(ret_5)"
