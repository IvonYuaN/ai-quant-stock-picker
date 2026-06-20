"""Double-gate notification checks, including held-out contamination blocking."""

from __future__ import annotations

import json

from aqsp.cli import _check_notification_gate, HELDOUT_TRAIN_CUTOFF


def _write_gate(tmp_path, **overrides):
    """写一个默认全过门的 sidecar，可用 overrides 覆盖字段。"""
    payload = {
        "run_date": "2026-06-01",
        "deflated_sharpe": 1.9,
        "pbo": 0.24,
        "pbo_valid": True,
        "dsr_pass": True,
        "pbo_pass": True,
        "both_pass": True,
        "data_start": "2018-01-01",
        "data_end": "2024-12-31",  # 边界内，干净
        "n_periods": 30,
    }
    payload.update(overrides)
    p = tmp_path / "gate.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


# ---- 正常放行 ----


def test_all_three_gates_pass(tmp_path, monkeypatch):
    """冷启动够 + DSR/PBO 过 + 数据窗口干净 → 放行。"""
    # 固定 today 避免过期检查把测试跑挂
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30, gate_path=_write_gate(tmp_path)
    )
    assert ok is True
    assert reasons == []


# ---- 三门各自拦截 ----


def test_cold_start_blocks(tmp_path, monkeypatch):
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=10, gate_path=_write_gate(tmp_path)
    )
    assert ok is False
    assert any("冷启动" in r for r in reasons)


def test_dsr_fail_blocks(tmp_path, monkeypatch):
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(tmp_path, dsr_pass=False, deflated_sharpe=0.5),
    )
    assert ok is False
    assert any("DSR" in r for r in reasons)


def test_pbo_fail_blocks(tmp_path, monkeypatch):
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(tmp_path, pbo_pass=False, pbo=0.8),
    )
    assert ok is False
    assert any("PBO" in r for r in reasons)


def test_pbo_placeholder_reports_grid_evidence_gap(tmp_path, monkeypatch):
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(
            tmp_path,
            pbo=0.0,
            pbo_valid=False,
            pbo_pass=False,
            both_pass=False,
        ),
    )

    assert ok is False
    assert any("单策略占位" in reason for reason in reasons)
    assert all("PBO 有效性标志" not in reason for reason in reasons)


def test_string_boolean_sidecar_flags_fail_closed(tmp_path, monkeypatch):
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(
            tmp_path,
            pbo_valid="true",
            dsr_pass="true",
            pbo_pass="true",
            both_pass="true",
        ),
    )

    assert ok is False
    assert any("内部通过标志" in reason for reason in reasons)


def test_both_pass_flag_must_be_true(tmp_path, monkeypatch):
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(tmp_path, both_pass=False),
    )

    assert ok is False
    assert any("内部通过标志" in reason for reason in reasons)


def test_malformed_n_periods_fail_closed(tmp_path, monkeypatch):
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(tmp_path, n_periods="many"),
    )

    assert ok is False
    assert any("n_periods=many" in reason for reason in reasons)


def test_boolean_n_periods_fail_closed(tmp_path, monkeypatch):
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(tmp_path, n_periods=True),
    )

    assert ok is False
    assert any("n_periods=True" in reason for reason in reasons)


def test_boolean_or_nan_metric_fields_fail_closed(tmp_path, monkeypatch):
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(tmp_path, deflated_sharpe=True, pbo="NaN"),
    )

    assert ok is False
    assert any("DSR 字段缺失" in reason for reason in reasons)
    assert any("PBO 字段缺失" in reason for reason in reasons)


def test_string_numeric_metric_fields_fail_closed(tmp_path, monkeypatch):
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(tmp_path, deflated_sharpe="1.9", pbo="0.24"),
    )

    assert ok is False
    assert any("DSR 字段缺失" in reason for reason in reasons)
    assert any("PBO 字段缺失" in reason for reason in reasons)


# ---- fail-closed：sidecar 缺失/解析失败/过期 ----


def test_missing_sidecar_fail_closed(tmp_path):
    ok, reasons = _check_notification_gate(
        cold_start_days=30, gate_path=str(tmp_path / "does_not_exist.json")
    )
    assert ok is False
    assert any("不存在" in r for r in reasons)


def test_corrupt_sidecar_fail_closed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    ok, reasons = _check_notification_gate(cold_start_days=30, gate_path=str(p))
    assert ok is False
    assert any("解析失败" in r for r in reasons)


def test_stale_sidecar_blocks(tmp_path, monkeypatch):
    """run_date 超过 35 天 → 过期拦截。"""
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(tmp_path, run_date="2026-01-01"),  # 半年前
    )
    assert ok is False
    assert any("过期" in r for r in reasons)


# ---- 核心：held-out 污染拦截（本轮修复的 bug）----


def test_heldout_poisoned_grade_blocked(tmp_path, monkeypatch):
    """DSR/PBO 都过门，但 data_end 越过 held-out 边界（说明成绩用了 held-out
    数据，可能经 --allow-heldout 跑出）→ 必须拦截，不得解锁推送。
    这是摊2/摊4 接缝盲区的回归测试。"""
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(
            tmp_path,
            run_date="2026-06-01",
            data_end="2026-04-30",  # 越过 2024-12-31 边界
            dsr_pass=True,
            pbo_pass=True,
        ),
    )
    assert ok is False
    assert any("held-out" in r for r in reasons), reasons


def test_dataend_exactly_cutoff_passes(tmp_path, monkeypatch):
    """data_end 正好等于边界 2024-12-31 → 不算越界，放行。"""
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(tmp_path, data_end=HELDOUT_TRAIN_CUTOFF),
    )
    assert ok is True
    assert reasons == []


def test_dataend_malformed_fail_closed(tmp_path, monkeypatch):
    """sidecar 的 data_end 格式异常 → fail-closed（看不懂就拦）。"""
    import aqsp.cli as cli_mod
    from datetime import date

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    ok, reasons = _check_notification_gate(
        cold_start_days=30,
        gate_path=_write_gate(tmp_path, data_end="2026/04/30"),  # 非 ISO
    )
    assert ok is False
    assert any("格式异常" in r for r in reasons)
