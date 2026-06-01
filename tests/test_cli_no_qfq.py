from __future__ import annotations

from pathlib import Path


def test_cli_does_not_request_qfq_adjustment_for_validation_paths() -> None:
    cli_source = Path("src/aqsp/cli.py").read_text(encoding="utf-8")
    assert 'adjust="qfq"' not in cli_source
