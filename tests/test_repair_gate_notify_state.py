from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from aqsp.core.time import today_shanghai
from scripts.repair_gate_notify_state import repair_gate_notify_state

# 双门 sidecar 超过 MAX_GATE_AGE_DAYS(35 天) 会被判"过期"并额外触发 gate_stale，
# 使指纹/状态偏离用例意图。这里让 run_date 始终贴近当天，避免固化日期随时间腐化。
# 注意：predictions 的 5 月日期必须保留，signal_days == 30 依赖这 30 个不同日期。
_RUN_DATE = (today_shanghai() - timedelta(days=1)).isoformat()
_DATA_END = (today_shanghai() - timedelta(days=3)).isoformat()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_repair_gate_notify_state_overwrites_stale_cold_start_entry(
    tmp_path: Path,
) -> None:
    _write_jsonl(
        tmp_path / "data" / "predictions.jsonl",
        [
            {
                "signal_date": f"2026-05-{day:02d}",
                "symbol": "600519",
                "status": "watch_only",
            }
            for day in range(1, 31)
        ],
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "walkforward_gate.json").write_text(
        json.dumps(
            {
                "run_date": _RUN_DATE,
                "deflated_sharpe": -0.5708,
                "pbo": 0.6,
                "pbo_valid": True,
                "dsr_pass": False,
                "pbo_pass": False,
                "both_pass": False,
                "n_periods": 19,
                "effective_symbols": 5157,
                "window_mode": "rolling_recent",
                "data_end": _DATA_END,
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
                    _RUN_DATE: {
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
    assert result["run_date"] == _RUN_DATE
    assert result["signal_days"] == 30
    assert payload["sent_by_date"][_RUN_DATE]["fingerprint"] == "dsr|pbo"


def test_repair_gate_notify_state_clears_file_when_gate_passes(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "data" / "predictions.jsonl",
        [
            {
                "signal_date": f"2026-05-{day:02d}",
                "symbol": "600519",
                "status": "watch_only",
            }
            for day in range(1, 31)
        ],
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "walkforward_gate.json").write_text(
        json.dumps(
            {
                "run_date": _RUN_DATE,
                "deflated_sharpe": 1.2,
                "pbo": 0.2,
                "pbo_valid": True,
                "dsr_pass": True,
                "pbo_pass": True,
                "both_pass": True,
                "n_periods": 19,
                "effective_symbols": 5157,
                "window_mode": "rolling_recent",
                "data_end": _DATA_END,
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
