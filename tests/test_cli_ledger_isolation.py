"""验证盘中任务(intraday/midday)的 ledger 写入隔离。

防止盘中信号污染收盘 predictions.jsonl 胜率统计。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_ledger_env(monkeypatch):
    monkeypatch.delenv("AQSP_LEDGER", raising=False)
    monkeypatch.delenv("AQSP_INTRADAY_LEDGER", raising=False)


def test_safe_write_ledger_redirects_intraday_from_formal() -> None:
    from aqsp.cli import _safe_write_ledger_path

    result = _safe_write_ledger_path("data/predictions.jsonl", task_id="intraday")
    assert result == "data/intraday_predictions.jsonl"


def test_safe_write_ledger_redirects_midday_from_formal() -> None:
    from aqsp.cli import _safe_write_ledger_path

    result = _safe_write_ledger_path("data/predictions.jsonl", task_id="midday")
    assert result == "data/intraday_predictions.jsonl"


def test_safe_write_ledger_keeps_custom_intraday_path() -> None:
    """运维脚本传的临时 ledger 路径不应被重定向。"""
    from aqsp.cli import _safe_write_ledger_path

    result = _safe_write_ledger_path("/tmp/intraday_batch.jsonl", task_id="intraday")
    assert result == "/tmp/intraday_batch.jsonl"


def test_safe_write_ledger_keeps_formal_for_daily_task() -> None:
    """非盘中任务写入正式 ledger 不受影响。"""
    from aqsp.cli import _safe_write_ledger_path

    result = _safe_write_ledger_path("data/predictions.jsonl", task_id="daily")
    assert result == "data/predictions.jsonl"


def test_safe_write_ledger_respects_env_override(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_LEDGER", "data/custom_formal.jsonl")
    monkeypatch.setenv("AQSP_INTRADAY_LEDGER", "data/custom_intraday.jsonl")

    from aqsp.cli import _safe_write_ledger_path

    result = _safe_write_ledger_path("data/custom_formal.jsonl", task_id="intraday")
    assert result == "data/custom_intraday.jsonl"


def test_safe_write_ledger_handles_empty_task_id() -> None:
    from aqsp.cli import _safe_write_ledger_path

    result = _safe_write_ledger_path("data/predictions.jsonl", task_id="")
    assert result == "data/predictions.jsonl"
