"""`scripts/promote_gate_sidecar.py` 的测试。

这个脚本补的是三段式的最后一环：runner 算出的双门判定此前**永远不生效**，
因为没有任何任务会把拉回的 `runner.walkforward_gate.json` 提升成 prod 生效的那一份。

关键约束：**判定未过门不算失败**（门禁本来就该 fail），只有**结构性**问题才拒绝提升。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "promote_gate_sidecar.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("promote_gate_sidecar", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["promote_gate_sidecar"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mod():
    return _load_module()


def _payload(**overrides):
    """以 prod 真实 sidecar 为模板（run_date 2026-09-06，判定为 FAIL）。"""
    payload = {
        "run_date": "2026-09-06",
        "deflated_sharpe": 1.4158,
        "pbo": 0.5913,
        "pbo_valid": True,
        "dsr_pass": True,
        "pbo_pass": False,
        "both_pass": False,
        "data_start": "2023-09-05",
        "data_end": "2026-09-04",
        "n_periods": 20,
        "effective_symbols": 4404,
        "production_gate_coverage": {
            "coverage_mode": "auto_recent_window",
            "stock_symbols": 5553,
            "covered_symbols": 4405,
            "listing_aware": True,
            "expected_trade_days": 727,
        },
    }
    payload.update(overrides)
    return payload


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---- 判定未过门：**必须放行** ----


def test_failing_verdict_is_still_promoted(mod, tmp_path):
    """DSR/PBO 未过门只是「策略没过门」，不是「sidecar 不可信」→ 必须提升。"""
    fetched = _write(tmp_path / "runner.walkforward_gate.json", _payload())
    ok, detail = mod.evaluate(fetched, today=date(2026, 9, 6), max_age_days=35)
    assert ok is True, detail


def test_insufficient_cscv_evidence_is_refused(mod, tmp_path):
    """变体数不足 ⇒ PBO 系统性上偏 ⇒ 这不是「一份可信的判定」，必须拒绝提升。

    （prod 那份 09-06 的旧 sidecar 正是这种：`stable`/5 变体，结构上永远过不了
    `MIN_CSCV_VARIANTS=8`，所以它不该被当成可信证据扶正。）
    """
    fetched = _write(
        tmp_path / "g.json",
        _payload(cscv_reliable=False, n_variants=5, both_pass=False),
    )
    ok, detail = mod.evaluate(fetched, today=date(2026, 9, 6), max_age_days=35)
    assert ok is False
    assert "变体不足" in detail


def test_placeholder_pbo_is_refused(mod, tmp_path):
    """`pbo_valid=false` 表示 PBO 是占位值（没经真实 CSCV）⇒ 缺证据，不是判定。"""
    fetched = _write(
        tmp_path / "g.json",
        _payload(pbo_valid=False, pbo=0.0, pbo_pass=False, both_pass=False),
    )
    ok, detail = mod.evaluate(fetched, today=date(2026, 9, 6), max_age_days=35)
    assert ok is False
    assert "pbo_valid" in detail


# ---- 结构性问题：**必须拒绝** ----


def test_malformed_data_end_is_refused(mod, tmp_path):
    fetched = _write(tmp_path / "g.json", _payload(data_end="2026/09/04"))
    ok, detail = mod.evaluate(fetched, today=date(2026, 9, 6), max_age_days=35)
    assert ok is False
    assert "data_end" in detail


def test_zero_periods_is_refused(mod, tmp_path):
    fetched = _write(tmp_path / "g.json", _payload(n_periods=0))
    ok, detail = mod.evaluate(fetched, today=date(2026, 9, 6), max_age_days=35)
    assert ok is False
    assert "n_periods" in detail


def test_stale_sidecar_is_refused(mod, tmp_path):
    """超过 max_age_days 的 sidecar 不能提升 —— 否则等于把过期证据扶正。"""
    fetched = _write(tmp_path / "g.json", _payload())
    ok, detail = mod.evaluate(fetched, today=date(2026, 11, 1), max_age_days=35)
    assert ok is False
    assert "stale" in detail


def test_missing_run_date_is_refused(mod, tmp_path):
    payload = _payload()
    payload.pop("run_date")
    fetched = _write(tmp_path / "g.json", payload)
    ok, detail = mod.evaluate(fetched, today=date(2026, 9, 6), max_age_days=35)
    assert ok is False
    assert "run_date" in detail


def test_unknown_blocker_fails_closed(mod, tmp_path):
    """否定式白名单：没见过的 blocker 一律拒绝（fail-closed），不能自动放行。"""
    assert mod._verdict_only_blockers(("DSR=0.1 <= 1.0",)) == []
    assert mod._verdict_only_blockers(("PBO=59.13% outside (0%, 50%)",)) == []
    assert mod._verdict_only_blockers(("brand new check failed",)) == [
        "brand new check failed"
    ]


# ---- 文件操作 ----


def test_promote_archives_previous_and_replaces(mod, tmp_path):
    target = _write(tmp_path / "walkforward_gate.json", {"run_date": "2026-08-01"})
    fetched = _write(tmp_path / "runner.walkforward_gate.json", _payload())
    archive = tmp_path / "archived"

    rc = mod.promote(
        fetched=fetched,
        target=target,
        archive_dir=archive,
        today=date(2026, 9, 6),
        max_age_days=35,
    )

    assert rc == 0
    assert json.loads(target.read_text(encoding="utf-8"))["run_date"] == "2026-09-06"
    archived = list(archive.glob("walkforward_gate.*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text(encoding="utf-8"))["run_date"] == "2026-08-01"


def test_promote_refuses_and_leaves_target_untouched(mod, tmp_path):
    target = _write(tmp_path / "walkforward_gate.json", {"run_date": "2026-08-01"})
    fetched = _write(tmp_path / "runner.walkforward_gate.json", _payload(n_periods=0))

    rc = mod.promote(
        fetched=fetched,
        target=target,
        archive_dir=tmp_path / "archived",
        today=date(2026, 9, 6),
        max_age_days=35,
    )

    assert rc == 3
    assert json.loads(target.read_text(encoding="utf-8"))["run_date"] == "2026-08-01"
    assert not (tmp_path / "archived").exists()


def test_dry_run_touches_nothing(mod, tmp_path):
    target = _write(tmp_path / "walkforward_gate.json", {"run_date": "2026-08-01"})
    fetched = _write(tmp_path / "runner.walkforward_gate.json", _payload())

    rc = mod.promote(
        fetched=fetched,
        target=target,
        archive_dir=tmp_path / "archived",
        today=date(2026, 9, 6),
        max_age_days=35,
        dry_run=True,
    )

    assert rc == 0
    assert json.loads(target.read_text(encoding="utf-8"))["run_date"] == "2026-08-01"
    assert not (tmp_path / "archived").exists()
