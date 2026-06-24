from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.generate_sample_debate import generate_sample_debate_data
from scripts.generate_cold_start_signals import generate_mock_signal
from scripts.manage_data_lifecycle import analyze_debate_file, clean_old_debates
from scripts.merge_server_ledgers import merge_ledgers

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def test_generate_sample_debate_data_uses_research_safe_wording_and_timezone(
    tmp_path, monkeypatch
) -> None:
    fixed_now = datetime(2026, 6, 10, 9, 30, 0, tzinfo=SHANGHAI_TZ)
    monkeypatch.setattr(
        "scripts.generate_sample_debate.now_shanghai",
        lambda: fixed_now,
    )

    output_path = tmp_path / "debate_results.jsonl"
    generate_sample_debate_data(output_path=output_path)

    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 3
    assert all(row["created_at"].endswith("+08:00") for row in rows)
    assert all(row["debate_date"] == "2026-06-10" for row in rows)
    assert all("建议关注" not in row["final_consensus"] for row in rows)
    assert all("建议观望" not in row["final_consensus"] for row in rows)
    assert all("保持原评级" not in row["final_consensus"] for row in rows)
    assert all("辩论建议" not in row["adjustment_reason"] for row in rows)
    assert all(
        "买入" not in " ".join(row["rounds"][0]["opinions"][0]["arguments"])
        for row in rows
    )


def test_generate_cold_start_signal_uses_research_safe_disclaimer(
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 6, 10, 9, 30, 0, tzinfo=SHANGHAI_TZ)
    monkeypatch.setattr(
        "scripts.generate_cold_start_signals.now_shanghai",
        lambda: fixed_now,
    )

    signal = generate_mock_signal("600519", "2026-06-10", 1500.0, 80.0)

    assert signal["created_at"].endswith("+08:00")
    assert "不构成交易指令或投资建议" in " ".join(signal["risks"])
    assert "不构成投资建议" not in " ".join(signal["risks"])


def test_manage_data_lifecycle_uses_shanghai_clock_for_analysis_and_cleanup(
    tmp_path, monkeypatch
) -> None:
    fixed_now = datetime(2026, 6, 10, 9, 30, 0, tzinfo=SHANGHAI_TZ)
    monkeypatch.setattr(
        "scripts.manage_data_lifecycle.now_shanghai",
        lambda: fixed_now,
    )

    debate_path = tmp_path / "debate_results.jsonl"
    debate_path.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "600519", "debate_date": "2026-06-01"}),
                json.dumps({"symbol": "000858", "debate_date": "2026-06-09"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    analysis = analyze_debate_file(debate_path)
    preview = clean_old_debates(debate_path, keep_days=5, dry_run=True)

    assert analysis["today"] == "2026-06-10"
    assert analysis["days_oldest"] == 9
    assert analysis["days_newest"] == 1
    assert preview["cutoff_date"] == "2026-06-05"
    assert preview["would_keep"] == 1
    assert preview["would_delete"] == 1


def test_merge_ledgers_backup_stamp_uses_shanghai_clock(tmp_path, monkeypatch) -> None:
    fixed_now = datetime(2026, 6, 10, 9, 30, 0, tzinfo=SHANGHAI_TZ)
    monkeypatch.setattr(
        "scripts.merge_server_ledgers.now_shanghai",
        lambda: fixed_now,
    )

    target = tmp_path / "predictions.jsonl"
    source = tmp_path / "ledger.jsonl"
    target.write_text(
        json.dumps({"signal_date": "2026-06-01", "symbol": "000001"}) + "\n",
        encoding="utf-8",
    )
    source.write_text(
        json.dumps({"signal_date": "2026-06-02", "symbol": "000002"}) + "\n",
        encoding="utf-8",
    )

    summary = merge_ledgers(target, source, backup=True)

    assert summary.backup_paths
    assert all("20260610-093000" in path.name for path in summary.backup_paths)


def test_runtime_output_scripts_use_atomic_writes() -> None:
    script_paths = (
        Path("scripts/run_production_walkforward_gate.py"),
        Path("scripts/merge_server_ledgers.py"),
        Path("scripts/render_dashboard.py"),
    )

    for path in script_paths:
        text = path.read_text(encoding="utf-8")
        assert "atomic_write_text" in text
        assert ".write_text(" not in text
