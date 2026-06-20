from __future__ import annotations

import json

from scripts.merge_server_ledgers import merge_ledgers, merge_rows


def test_merge_rows_preserves_target_order_and_deduplicates() -> None:
    target_rows = [
        {
            "signal_date": "2026-06-01",
            "symbol": "000001",
            "thresholds_version": "1.1.0",
            "regime_at_signal": "trend",
            "intended_entry": "next_open",
            "status": "pending",
        }
    ]
    source_rows = [
        {
            "signal_date": "2026-06-01",
            "symbol": "000001",
            "thresholds_version": "1.1.0",
            "regime_at_signal": "trend",
            "intended_entry": "next_open",
            "status": "pending",
        },
        {
            "signal_date": "2026-06-02",
            "symbol": "000002",
            "thresholds_version": "1.1.0",
            "regime_at_signal": "trend",
            "intended_entry": "next_open",
            "status": "validated",
        },
    ]

    merged = merge_rows(target_rows, source_rows)

    assert len(merged) == 2
    assert merged[0]["symbol"] == "000001"
    assert merged[1]["symbol"] == "000002"


def test_merge_ledgers_fills_missing_signal_day_group_and_counts_days(
    tmp_path,
) -> None:
    target = tmp_path / "predictions.jsonl"
    source = tmp_path / "ledger.jsonl"
    target.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-01",
                "symbol": "000001",
                "thresholds_version": "1.1.0",
                "regime_at_signal": "trend",
                "intended_entry": "next_open",
                "status": "pending",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "signal_date": "2026-06-02",
                        "symbol": "000002",
                        "thresholds_version": "1.1.0",
                        "regime_at_signal": "trend",
                        "intended_entry": "next_open",
                        "status": "validated",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "signal_date": "2026-06-03",
                        "symbol": "000003",
                        "thresholds_version": "1.1.0",
                        "regime_at_signal": "trend",
                        "intended_entry": "next_open",
                        "status": "pending",
                        "is_simulated": True,
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = merge_ledgers(target, source, backup=False)

    merged_rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert summary.target_rows == 1
    assert summary.source_rows == 2
    assert summary.merged_rows == 3
    assert summary.cold_start_days == 2
    assert merged_rows[0]["signal_day_group"] == "2026-06-01"
    assert merged_rows[1]["signal_day_group"] == "2026-06-02"
