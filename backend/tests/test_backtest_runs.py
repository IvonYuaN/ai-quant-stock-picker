from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import app as app_module
import backtest_runs
import backtest_run_worker
import tenant


def _config(**overrides):
    payload = {
        "strategy_id": "composite",
        "symbols": ["600000", "000001"],
        "start": "2024-01-01",
        "end": "2026-10-30",
        "train_days": 120,
        "test_days": 30,
        "horizon_days": 3,
    }
    payload.update(overrides)
    return payload


def test_backtest_run_rejects_future_unsupported_or_oversized_inputs():
    with pytest.raises(backtest_runs.BacktestRunError, match="已注册"):
        backtest_runs._validate_request(**_config(strategy_id="unknown"))
    with pytest.raises(backtest_runs.BacktestRunError, match="六位"):
        backtest_runs._validate_request(**_config(symbols=["600000; rm"]))
    with pytest.raises(backtest_runs.BacktestRunError, match="最多三年"):
        backtest_runs._validate_request(**_config(start="2010-01-01"))
    with pytest.raises(backtest_runs.BacktestRunError, match="最近已完成交易日"):
        backtest_runs._validate_request(**_config(start="2027-01-01", end="2029-01-01"))


def test_backtest_run_latest_data_asof_reads_sqlite_without_writes(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "raw.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE stocks (ts_code TEXT NOT NULL)")
        connection.execute("CREATE TABLE daily_qfq (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL)")
        connection.executemany("INSERT INTO stocks VALUES (?)", [("600000.SH",), ("000001.SZ",)])
        connection.executemany(
            "INSERT INTO daily_qfq VALUES (?, ?)",
            [("600000.SH", "20260911"), ("000001.SZ", "20260930")],
        )
    monkeypatch.setattr(backtest_runs, "resolve_sqlite_db_path", lambda: str(db_path))
    with pytest.raises(backtest_runs.BacktestRunError, match="600000: 2026-09-11"):
        backtest_runs._latest_sqlite_data_asof(["600000"], "2026-09-30")
    assert backtest_runs._latest_sqlite_data_asof(["600000"], "2026-09-11") == (
        "2026-09-11", {"600000": "2026-09-11"}
    )
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM daily_qfq").fetchone()[0] == 2


def test_backtest_run_start_rejects_end_after_real_sqlite_asof(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path)
    monkeypatch.setattr(backtest_runs, "_latest_sqlite_data_asof", lambda *_args: (_ for _ in ()).throw(backtest_runs.BacktestRunError("所选标的未覆盖结束日前市场最新交易日 2026-09-11（600000: 2026-09-11）")))
    with pytest.raises(backtest_runs.BacktestRunError, match="未覆盖结束日前市场最新交易日"):
        backtest_runs.start_run(**_config())


def test_backtest_run_start_persists_fixed_isolated_configuration(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path)
    monkeypatch.setattr(backtest_runs, "_latest_sqlite_data_asof", lambda symbols, _end: ("2026-10-30", {symbol: "2026-10-30" for symbol in symbols}))

    class Process:
        pid = 12345

    captured = {}

    def popen(args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return Process()

    monkeypatch.setattr(backtest_runs.subprocess, "Popen", popen)
    monkeypatch.setattr(backtest_runs, "_active_process", lambda _state: False)
    result = backtest_runs.start_run(**_config())

    state = json.loads((tmp_path / result["run_id"] / "state.json").read_text())
    assert result["status"] == "running"
    assert state["config_hash"]
    assert state["pid"] == 12345
    assert "--run-id" in captured["args"]
    assert "-m" not in captured["args"]
    assert captured["kwargs"]["start_new_session"] is True
    assert state["config"]["source"] == "sqlite_db"
    assert state["config"]["grid_profile"] == "stable_plus"
    assert state["config"]["data_as_of"] == "2026-10-30"
    assert state["config"]["symbol_data_asof"] == {"600000": "2026-10-30", "000001": "2026-10-30"}


def test_backtest_run_rejects_parallel_active_task(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path)
    monkeypatch.setattr(backtest_runs, "_latest_sqlite_data_asof", lambda symbols, _end: ("2026-10-30", {symbol: "2026-10-30" for symbol in symbols}))
    prior = tmp_path / "prior" / "state.json"
    prior.parent.mkdir()
    prior.write_text(json.dumps({"run_id": "prior", "status": "running", "pid": 1}))
    monkeypatch.setattr(backtest_runs, "_active_process", lambda _state: True)

    with pytest.raises(backtest_runs.BacktestRunConflict):
        backtest_runs.start_run(**_config())


def test_backtest_run_marks_orphaned_state_unknown_and_scopes_tenant(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_user_data_root", lambda: tmp_path)
    monkeypatch.setattr(backtest_runs, "_active_process", lambda _state: False)
    token = tenant.current_tenant.set("u_0123456789abcdef0123")
    try:
        path = tmp_path / "users" / tenant.current_tenant.get() / "backtest-runs" / ("a" * 32)
        path.mkdir(parents=True)
        (path / "state.json").write_text(json.dumps({"run_id": "a" * 32, "status": "running", "pid": 9876}))
        result = backtest_runs.get_run("a" * 32)
        assert result is not None
        assert result["status"] == "unknown"
        assert backtest_runs.get_run("b" * 32) is None
    finally:
        tenant.current_tenant.reset(token)


def test_backtest_run_report_is_available_only_after_completion(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path)
    run_dir = tmp_path / ("c" * 32)
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"run_id": "c" * 32, "status": "running"}))
    (run_dir / "report.md").write_text("完成报告")
    assert backtest_runs.report_text("c" * 32) is None
    (run_dir / "state.json").write_text(json.dumps({"run_id": "c" * 32, "status": "completed"}))
    assert backtest_runs.report_text("c" * 32) == "完成报告"
    assert backtest_runs.delete_run("c" * 32) is True
    assert not run_dir.exists()


def test_backtest_run_retention_cap_never_silently_deletes_saved_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path)
    monkeypatch.setattr(backtest_runs, "_latest_sqlite_data_asof", lambda symbols, _end: ("2026-10-30", {symbol: "2026-10-30" for symbol in symbols}))
    for index in range(backtest_runs.MAX_RETAINED_RUNS):
        run_id = f"{index:032x}"
        run_dir = tmp_path / run_id
        run_dir.mkdir()
        (run_dir / "state.json").write_text(json.dumps({"run_id": run_id, "status": "completed"}))
    with pytest.raises(backtest_runs.BacktestRunConflict, match="删除"):
        backtest_runs.start_run(**_config())
    assert len(list(tmp_path.glob("*/state.json"))) == backtest_runs.MAX_RETAINED_RUNS


def test_backtest_run_cannot_delete_active_task(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path)
    run_dir = tmp_path / ("7" * 32)
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"run_id": "7" * 32, "status": "running"}))
    with pytest.raises(backtest_runs.BacktestRunConflict, match="不能删除"):
        backtest_runs.delete_run("7" * 32)


def test_backtest_run_worker_persists_terminal_result_and_artifact(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path)
    run_id = "f" * 32
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    state_path = run_dir / "state.json"
    config = backtest_runs._validate_request(**_config())
    state_path.write_text(json.dumps({"run_id": run_id, "pid": os.getpid(), "status": "starting", "config": config}))

    class FinishedChild:
        def __init__(self):
            read_fd, write_fd = os.pipe()
            os.write(write_fd, b"loading raw bars\nWalk-forward complete\n")
            os.close(write_fd)
            self.stdout = os.fdopen(read_fd, "rb", buffering=0)
            self.poll_calls = 0

        def poll(self):
            self.poll_calls += 1
            return None if self.poll_calls == 1 else 0

        def wait(self):
            return 0

    def popen(*_args, **_kwargs):
        (run_dir / "report.md").write_text("# Walk-forward report")
        (run_dir / "gate.json").write_text(json.dumps({
            "run_date": "2026-11-03", "both_pass": False,
            "grid_diagnostics": {"cscv_reliability": "degraded", "cscv_warnings": ["small blocks"]},
        }))
        evidence_dir = run_dir / "runtime" / "strategy_oos_evidence" / "composite"
        evidence_dir.mkdir(parents=True)
        (evidence_dir / "artifact.json").write_text(json.dumps({
            "artifact_id": "artifact-1", "evidence_status": "blocked",
            "effective_symbols": 1, "blockers": ["too few symbols"],
        }))
        return FinishedChild()

    monkeypatch.setattr(backtest_run_worker.subprocess, "Popen", popen)
    assert backtest_run_worker.run_worker(run_id, state_path, tmp_path) == 0
    result = json.loads(state_path.read_text())
    assert result["status"] == "completed"
    assert result["artifact_id"]
    assert result["gate"]["both_pass"] is False
    assert result["gate"]["cscv_reliability"] == "degraded"
    assert result["oos_evidence"]["evidence_status"] == "blocked"
    assert result["oos_evidence"]["blockers"] == ["too few symbols"]
    assert backtest_runs.report_text(run_id) == "# Walk-forward report"


@pytest.mark.parametrize(("signal_cancel", "max_seconds", "expected"), [(True, 10, "cancelled"), (False, 0, "timed_out")])
def test_backtest_run_worker_reaps_child_before_cancelled_or_timeout_terminal(
    tmp_path: Path, monkeypatch, signal_cancel: bool, max_seconds: int, expected: str
):
    run_id = "9" * 32
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    state_path = run_dir / "state.json"
    config = backtest_runs._validate_request(**_config())
    state_path.write_text(json.dumps({"run_id": run_id, "pid": os.getpid(), "status": "starting", "config": config}))

    class Child:
        pid = 887766

        def __init__(self):
            read_fd, write_fd = os.pipe()
            os.close(write_fd)
            self.stdout = os.fdopen(read_fd, "rb", buffering=0)
            self.poll_calls = 0
            self.wait_calls = 0

        def poll(self):
            self.poll_calls += 1
            return None

        def wait(self, timeout=None):
            self.wait_calls += 1
            if timeout is not None:
                if signal_cancel:
                    return -15
                raise backtest_run_worker.subprocess.TimeoutExpired("fake-child", timeout)
            return -15

    child = Child()

    def popen(*_args, **_kwargs):
        if signal_cancel:
            backtest_run_worker._request_cancel(15, None)
        return child

    kills = []
    monkeypatch.setattr(backtest_run_worker.subprocess, "Popen", popen)
    monkeypatch.setattr(backtest_run_worker.os, "killpg", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(backtest_run_worker, "MAX_RUN_SECONDS", max_seconds)
    backtest_run_worker.run_worker(run_id, state_path, tmp_path)

    result = json.loads(state_path.read_text())
    assert result["status"] == expected
    assert result["progress"] is None
    assert child.wait_calls == 2 if not signal_cancel else child.wait_calls == 1
    assert kills[0][0] == child.pid


def test_backtest_run_cancel_stays_cancelling_until_worker_finishes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path)
    monkeypatch.setattr(backtest_runs, "_active_process", lambda _state: True)
    run_id = "a" * 32
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps({"run_id": run_id, "status": "running", "pid": 5678}))
    sent = []
    monkeypatch.setattr(backtest_runs.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    result = backtest_runs.cancel_run(run_id)
    assert result is not None
    assert result["status"] == "cancelling"
    assert sent == [(5678, backtest_runs.signal.SIGTERM)]
    with pytest.raises(backtest_runs.BacktestRunConflict, match="不能删除"):
        backtest_runs.delete_run(run_id)


def test_backtest_run_api_validates_and_exposes_task_contract(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path)
    monkeypatch.setattr(backtest_runs, "_latest_sqlite_data_asof", lambda symbols, _end: ("2026-10-30", {symbol: "2026-10-30" for symbol in symbols}))

    class Process:
        pid = 12345

    monkeypatch.setattr(backtest_runs.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    monkeypatch.setattr(backtest_runs, "_active_process", lambda _state: True)
    client = TestClient(app_module.app)

    response = client.post("/api/aqsp/backtest-runs", json=_config())
    listed = client.get("/api/aqsp/backtest-runs")
    invalid = client.post("/api/aqsp/backtest-runs", json={**_config(), "symbols": ["600000;rm"]})
    run_id = response.json()["data"]["run_id"]
    restored = client.get(f"/api/aqsp/backtest-runs/{run_id}")

    assert response.status_code == 200
    assert (tmp_path / run_id / "state.json").is_file()
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["run_id"] == run_id
    assert restored.status_code == 200
    assert restored.json()["data"]["status"] == "running"
    assert invalid.status_code == 422


def test_backtest_run_api_rejects_request_beyond_sqlite_data_asof(monkeypatch):
    monkeypatch.setattr(backtest_runs, "_latest_sqlite_data_asof", lambda *_args: (_ for _ in ()).throw(backtest_runs.BacktestRunError("所选标的未覆盖结束日前市场最新交易日 2026-09-11（600000: 2026-09-11）")))
    response = TestClient(app_module.app).post("/api/aqsp/backtest-runs", json=_config())
    assert response.status_code == 400
    assert "未覆盖结束日前市场最新交易日 2026-09-11" in response.json()["detail"]


def test_backtest_run_cancel_does_not_touch_foreign_tenant_data(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path / "one")
    other = tmp_path / "two" / ("e" * 32)
    other.mkdir(parents=True)
    (other / "state.json").write_text(json.dumps({"run_id": "e" * 32, "status": "running", "pid": 123}))
    token = tenant.current_tenant.set("u_0123456789abcdef0123")
    try:
        assert backtest_runs.cancel_run("e" * 32) is None
        assert json.loads((other / "state.json").read_text())["status"] == "running"
    finally:
        tenant.current_tenant.reset(token)


def test_backtest_run_delete_api_removes_only_terminal_tenant_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backtest_runs, "_tenant_root", lambda: tmp_path)
    run_id = "8" * 32
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"run_id": run_id, "status": "completed"}))
    (run_dir / "report.md").write_text("private artifact")
    response = TestClient(app_module.app).delete(f"/api/aqsp/backtest-runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["data"]["deleted"] is True
    assert not run_dir.exists()
