from __future__ import annotations

import json
from pathlib import Path

from scripts.resolve_notify_level import resolve


def test_resolve_notify_level_reads_latest_source_health_row(tmp_path: Path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    ledger.write_text(
        "\n".join(
            [
                json.dumps({"source_health_label": "healthy"}),
                json.dumps({"run_source_health_label": "degraded", "source_route": "fallback"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert resolve(ledger, "label") == "degraded"
    assert resolve(ledger, "level") == "critical"
    assert resolve(ledger, "route") == "fallback"
