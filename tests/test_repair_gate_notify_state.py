from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from aqsp.core.time import today_shanghai

from scripts.repair_gate_notify_state import repair_gate_notify_state

# 双门 sidecar 的新鲜度上限为 MAX_GATE_AGE_DAYS=35 天；fixture 必须使用相对“今天”的日期，
# 否则 gate 会被判为过期(stale)而 fail-closed，测试随时间推移必然失败。
def _fresh_gate_dates():
    today = today_shanghai()
    return (today - timedelta(days=1)).isoformat(), (today - timedelta(days=4)).isoformat()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_repair_gate_notify_state_overwrites_stale_cold_start_entry(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "data" / "predictions.jsonl",
        [
            {"signal_date": f"2026-05-{day:02d}", "symbol": "600519", "status": "watch_only"}
            for day in range(1, 31)
        ],
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    _run_date, _data_end = _fresh_gate_dates()
    (tmp_path / "data" / "walkforward_gate.json").write_text(
        json.dumps(
            {
                "run_date": _run_date,
                "deflated_sharpe": -0.5708,
                "pbo": 0.6,
                "pbo_valid": True,
                "dsr_pass": False,
                "pbo_pass": False,
                "both_pass": False,
                "n_periods": 19,
                "effective_symbols": 5157,
                "window_mode": "rolling_recent",
                "data_end": _data_end,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "gate_notify_state.json").write_text(
        json.dumps(
            {
                "sent_by_date": {
                    _run_date: {
                        "fingerprint": "cold_start|n_periods_invalid",
                        "status": "suppressed",
                    }
                }
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = repair_gate_notify_state(tmp_path)
    payload = json.loads(
        (tmp_path / "data" / "gate_notify_state.json").read_text(encoding="utf-8")
    )

    assert result["status"] == "suppressed"
    assert result["run_date"] == _run_date
    assert result["signal_days"] == 30
    assert payload["sent_by_date"][_run_date]["fingerprint"] == "dsr|pbo"


def test_repair_gate_notify_state_clears_file_when_gate_passes(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "data" / "predictions.jsonl",
        [
            {"signal_date": f"2026-05-{day:02d}", "symbol": "600519", "status": "watch_only"}
            for day in range(1, 31)
        ],
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    _run_date, _data_end = _fresh_gate_dates()
    (tmp_path / "data" / "walkforward_gate.json").write_text(
        json.dumps(
            {
                "run_date": _run_date,
                "deflated_sharpe": 1.2,
                "pbo": 0.2,
                "pbo_valid": True,
                "dsr_pass": True,
                "pbo_pass": True,
                "both_pass": True,
                "n_periods": 19,
                "effective_symbols": 5157,
                "window_mode": "rolling_recent",
                "data_end": _data_end,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    state_path = tmp_path / "data" / "gate_notify_state.json"
    state_path.write_text('{"status":"suppressed"}\n', encoding="utf-8")

    result = repair_gate_notify_state(tmp_path)

    assert result["status"] == "cleared"
    assert result["gate_reasons"] == []
    assert not state_path.exists()
