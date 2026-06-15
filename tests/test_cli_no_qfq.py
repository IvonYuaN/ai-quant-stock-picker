from __future__ import annotations

from pathlib import Path


def test_cli_does_not_request_qfq_adjustment_for_validation_paths() -> None:
    cli_source = Path("src/aqsp/cli.py").read_text(encoding="utf-8")
    assert 'adjust="qfq"' not in cli_source


def test_fetcher_example_defaults_to_unadjusted_prices() -> None:
    example_source = Path("scripts/example_fetcher_usage.py").read_text(
        encoding="utf-8"
    )
    assert 'adjust="qfq"' not in example_source
    assert 'adjust=""' in example_source
