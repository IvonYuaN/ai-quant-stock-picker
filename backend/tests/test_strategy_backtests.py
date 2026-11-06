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


class CoverageSource(ReplaySource):
    """逐标的 as-of 覆盖：用于验证「一键复测」严格覆盖、不静默降级。"""

    def __init__(self, per_symbol):
        super().__init__()
        self.per_symbol = per_symbol

    def get_latest_daily_dates(self, symbols, *, as_of=None):
        return {symbol: self.per_symbol.get(symbol) for symbol in symbols}


class NoMarketDateSource(ReplaySource):
    """数据层 fail-closed：市场级覆盖不足时 get_latest_daily_date() 返回 None。"""

    def get_latest_daily_date(self):
        return None


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


# ---------------------------------------------------------------------------
# 保存候选回测参数与一键复测（阶段 A 缺口）
# ---------------------------------------------------------------------------

OTHER_TENANT = "u_" + "b" * 20


def _seed_run(monkeypatch, *, symbols=None, mode="single"):
    """Create one frozen run config.json via a fully-covered synthetic source."""
    strategy = _save_strategy()
    source = ReplaySource()
    monkeypatch.setattr(strategy_backtests, "build_data_source", lambda *_a, **_k: source)
    monkeypatch.setattr(
        strategy_backtests.subprocess, "Popen",
        lambda *_a, **_k: SimpleNamespace(pid=987654),
    )
    run = strategy_backtests.start_run(
        strategy_id=strategy["id"],
        symbols=symbols or ["600001"],
        signal_start="2026-10-26",
        signal_end="2026-10-28",
        outcome_as_of="2026-10-30",
        mode=mode,
        initial_capital=100_000 if mode == "portfolio" else None,
    )
    config = strategy_backtests._read(
        strategy_backtests._run_dir(run["run_id"]) / "config.json"
    )
    return run["run_id"], config


def _run_ids():
    return sorted(
        path.parent.name
        for path in strategy_backtests._root().glob("*/state.json")
    )


def _mark_completed(run_id):
    path = strategy_backtests._state_path(run_id)
    state = strategy_backtests._read(path)
    state["status"] = "completed"
    strategy_backtests._write(path, state)


def _write_gate_result(run_id):
    strategy_backtests._write(
        strategy_backtests._run_dir(run_id) / "result.json",
        {
            "status": "completed", "win_rate": 0.9,
            "gate": {"status": "pass", "dsr": 2.1},
            "oos_evidence": {"artifact_id": "old-artifact"},
        },
    )


def test_save_config_crud_cas_and_tenant_isolation(replay_store, monkeypatch):
    _, config = _seed_run(monkeypatch)
    saved = strategy_backtests.save_config(
        name="候选 A", config=config, config_id="cand_a"
    )
    assert saved["revision"] == 1
    assert saved["params_sha256"]
    assert saved["strategy_hash"] == config["strategy_hash"]
    assert saved["symbols"] == ["600001"]
    assert strategy_backtests.get_config("cand_a")["name"] == "候选 A"
    assert any(item["config_id"] == "cand_a" for item in strategy_backtests.list_configs())

    # 配置与证据分离：把结果/gate 注入输入，白名单仍只保存回放参数。
    polluted = dict(config)
    polluted.update({
        "result": {"win_rate": 0.9}, "gate": {"status": "pass"},
        "oos_evidence": {"artifact_id": "x"}, "win_rate": 0.9,
    })
    evidence_free = strategy_backtests.save_config(
        name="证据隔离", config=polluted, config_id="cand_evidence"
    )
    for forbidden in ("result", "gate", "oos_evidence", "win_rate"):
        assert forbidden not in evidence_free

    # CAS 冲突 + 更新
    with pytest.raises(strategy_backtests.StrategyBacktestConfigConflict):
        strategy_backtests.save_config(
            name="候选 A2", config=config, config_id="cand_a", expected_revision=0
        )
    updated = strategy_backtests.save_config(
        name="候选 A2", config=config, config_id="cand_a", expected_revision=1
    )
    assert updated["revision"] == 2
    assert updated["name"] == "候选 A2"
    assert len(updated["audit"]) == 2

    # 租户隔离
    token = tenant.current_tenant.set(OTHER_TENANT)
    try:
        assert strategy_backtests.get_config("cand_a") is None
        assert strategy_backtests.list_configs() == []
    finally:
        tenant.current_tenant.reset(token)

    assert strategy_backtests.delete_config("cand_a", 2) is True
    assert strategy_backtests.get_config("cand_a") is None


def test_save_config_from_completed_run_does_not_inherit_gate(replay_store, monkeypatch):
    run_id, _ = _seed_run(monkeypatch)
    _write_gate_result(run_id)

    # 未完成的 run 不允许保存为候选配置
    with pytest.raises(strategy_backtests.StrategyBacktestError, match="已完成"):
        strategy_backtests.save_config_from_run(
            run_id=run_id, name="未完成", config_id="from_run"
        )

    _mark_completed(run_id)
    saved = strategy_backtests.save_config_from_run(
        run_id=run_id, name="来自已完成回测", config_id="from_run"
    )
    assert saved["source_run_id"] == run_id
    assert saved["origin"] == "saved_from_run"
    # 旧 run 的 result.json 里的 gate/胜率/证据一律不进入配置
    for forbidden in ("result", "gate", "oos_evidence", "win_rate", "trades"):
        assert forbidden not in saved
    assert saved["policy"]["thresholds_version"]
    assert saved["strategy"]["expression"] == "close > ma20"


def test_rerun_success_creates_fresh_run_without_old_gate(replay_store, monkeypatch):
    run_id, _ = _seed_run(monkeypatch)
    _mark_completed(run_id)
    _write_gate_result(run_id)
    strategy_backtests.save_config_from_run(
        run_id=run_id, name="复测成功", config_id="rerun_ok"
    )

    before = _run_ids()
    # 复测使用同一合成源：每个标的都覆盖结果数据日 -> 严格校验通过
    outcome = strategy_backtests.rerun_from_config(
        config_id="rerun_ok", expected_revision=1
    )
    assert outcome["outcome"] == "started"
    new_run_id = outcome["run_id"]
    assert len(new_run_id) == 32
    assert outcome["run"]["status"] == "running"
    assert outcome["run"]["saved_config_id"] == "rerun_ok"
    assert outcome["run"]["rerun_of"] == run_id
    assert outcome["terminal"] is False
    # 新 run 与 outcome 都不继承旧 gate/结果
    for forbidden in ("result", "gate", "win_rate", "oos_evidence"):
        assert forbidden not in outcome
    new_config = strategy_backtests._read(
        strategy_backtests._run_dir(new_run_id) / "config.json"
    )
    new_state = strategy_backtests._read(
        strategy_backtests._state_path(new_run_id)
    )
    for forbidden in ("result", "gate", "win_rate"):
        assert forbidden not in new_config
        assert forbidden not in new_state
    assert new_config["symbols"] == ["600001"]
    # 恰好新增一个 run，且可读（终态可读路径）
    assert sorted(before + [new_run_id]) == _run_ids()
    assert strategy_backtests.get_run(new_run_id) is not None
    # 配置仍在，审计记录本次成功、终态不自动重跑
    config = strategy_backtests.get_config("rerun_ok")
    assert config["last_rerun"]["outcome"] == "started"
    assert config["last_rerun"]["terminal"] is False
    assert any(event["action"] == "rerun_started" for event in config["audit"])


def test_rerun_rejects_missing_asof_coverage_without_run_and_recovers(
    replay_store, monkeypatch
):
    _, config = _seed_run(monkeypatch, symbols=["600001", "600002"], mode="portfolio")
    strategy_backtests.save_config(
        name="组合缺口", config=config, config_id="rerun_gap"
    )

    # 结束日前 600002 陈旧：普通组合回放会静默丢弃，一键复测必须明确拒绝
    stale_source = CoverageSource({
        "600001": date(2026, 10, 30),
        "600002": date(2026, 10, 29),
    })
    monkeypatch.setattr(
        strategy_backtests, "build_data_source", lambda *_a, **_k: stale_source
    )
    before = _run_ids()
    outcome = strategy_backtests.rerun_from_config(config_id="rerun_gap")
    assert outcome["outcome"] == "rejected"
    assert outcome["run_id"] is None
    assert outcome["run"] is None
    assert outcome["terminal"] is True
    assert "未创建回放任务" in outcome["message"]
    assert any(
        gap["symbol"] == "600002" and gap["reason"] == "stale"
        and gap["latest_as_of"] == "2026-10-29"
        and gap["required_as_of"] == "2026-10-30"
        for gap in outcome["coverage_gaps"]
    )
    # 不产生 run，不静默降级
    assert _run_ids() == before
    # 失败后配置可恢复：仍在，终态可读，审计留痕
    failed_config = strategy_backtests.get_config("rerun_gap")
    assert failed_config["revision"] == 1
    assert failed_config["last_rerun"]["terminal"] is True
    assert any(event["action"] == "rerun_rejected" for event in failed_config["audit"])

    # 数据补齐后同一配置可继续复测（recoverable），不自动重跑
    monkeypatch.setattr(
        strategy_backtests, "build_data_source", lambda *_a, **_k: ReplaySource()
    )
    recovered = strategy_backtests.rerun_from_config(config_id="rerun_gap")
    assert recovered["outcome"] == "started"
    assert recovered["run_id"] is not None
    assert len(_run_ids()) == len(before) + 1


def test_rerun_data_source_unavailable_is_terminal_rejection(replay_store, monkeypatch):
    _, config = _seed_run(monkeypatch)
    strategy_backtests.save_config(
        name="数据不可用", config=config, config_id="rerun_no_data"
    )

    def _broken(*_a, **_k):
        raise RuntimeError("sqlite 行情库不可用")

    monkeypatch.setattr(strategy_backtests, "build_data_source", _broken)
    before = _run_ids()
    outcome = strategy_backtests.rerun_from_config(config_id="rerun_no_data")
    assert outcome["outcome"] == "rejected"
    assert outcome["run_id"] is None
    assert outcome["market_as_of"] is None
    assert outcome["terminal"] is True
    assert "不可用" in outcome["message"]
    assert _run_ids() == before
    assert strategy_backtests.get_config("rerun_no_data")["last_rerun"]["terminal"] is True


def test_rerun_cas_conflict_and_tenant_scope(replay_store, monkeypatch):
    _, config = _seed_run(monkeypatch)
    strategy_backtests.save_config(
        name="CAS 配置", config=config, config_id="cas_cfg"
    )
    strategy_backtests.save_config(
        name="CAS 配置 v2", config=config, config_id="cas_cfg", expected_revision=1
    )
    # 复测带过期 revision -> CAS 冲突
    with pytest.raises(strategy_backtests.StrategyBacktestConfigConflict):
        strategy_backtests.rerun_from_config(
            config_id="cas_cfg", expected_revision=1
        )
    # 其他租户看不到配置，也不能复测
    token = tenant.current_tenant.set(OTHER_TENANT)
    try:
        assert strategy_backtests.get_config("cas_cfg") is None
        with pytest.raises(strategy_backtests.StrategyBacktestError, match="已移出当前租户"):
            strategy_backtests.rerun_from_config(config_id="cas_cfg")
    finally:
        tenant.current_tenant.reset(token)


def test_rerun_worker_failure_leaves_terminal_readable_run(replay_store, monkeypatch):
    """覆盖校验通过但 worker 无法启动：run 落终态、可读，不自动续跑。"""
    _, config = _seed_run(monkeypatch)
    strategy_backtests.save_config(
        name="worker 失败", config=config, config_id="rerun_worker_fail"
    )

    def _broken_popen(*_a, **_k):
        raise OSError("worker 无法启动")

    monkeypatch.setattr(strategy_backtests.subprocess, "Popen", _broken_popen)
    before = set(_run_ids())
    with pytest.raises(strategy_backtests.StrategyBacktestError, match="无法启动"):
        strategy_backtests.rerun_from_config(config_id="rerun_worker_fail")
    created = set(_run_ids()) - before
    assert len(created) == 1
    failed_run_id = created.pop()
    state = strategy_backtests._read(strategy_backtests._state_path(failed_run_id))
    assert state["status"] == "failed"
    assert strategy_backtests.get_run(failed_run_id) is not None
    # 失败路径可恢复：配置仍在，可再次复测
    assert strategy_backtests.get_config("rerun_worker_fail") is not None


def test_get_latest_daily_date_none_is_fail_closed_for_start_and_rerun(
    replay_store, monkeypatch
):
    """数据层返回 None（市场覆盖不足）时，普通回放与一键复测都明确失败、不崩溃。

    主线程已把数据源 get_latest_daily_date() 改成覆盖不足返回 None；此用例证明
    _preflight 的 `market_latest is None or ...` 短路不会触发 date > None 的 TypeError。
    """
    _, config = _seed_run(monkeypatch)
    strategy_backtests.save_config(
        name="覆盖不足", config=config, config_id="rerun_no_market"
    )
    monkeypatch.setattr(
        strategy_backtests, "build_data_source",
        lambda *_a, **_k: NoMarketDateSource(),
    )

    # 普通 start_run：明确失败，不抛 TypeError
    with pytest.raises(strategy_backtests.StrategyBacktestError):
        strategy_backtests.start_run(
            strategy_id="entry_exit_demo",
            symbols=["600001"],
            signal_start="2026-10-26",
            signal_end="2026-10-28",
            outcome_as_of="2026-10-30",
        )

    # 一键复测：结构化 rejected（终态、不产生 run、market_as_of=None）
    before = _run_ids()
    outcome = strategy_backtests.rerun_from_config(config_id="rerun_no_market")
    assert outcome["outcome"] == "rejected"
    assert outcome["run_id"] is None
    assert outcome["market_as_of"] is None
    assert outcome["terminal"] is True
    assert _run_ids() == before
    assert strategy_backtests.get_config("rerun_no_market") is not None
