from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_intraday_batch.py"
SPEC = importlib.util.spec_from_file_location("prepare_intraday_batch", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_filter_intraday_history_symbols_excludes_unusable_beijing_history() -> None:
    symbols = ["000001", "688612", "920186", "920992"]

    assert MODULE.filter_intraday_history_symbols(symbols, "online_first") == [
        "000001",
        "688612",
    ]


def test_filter_intraday_history_symbols_keeps_beijing_for_supported_source() -> None:
    symbols = ["000001", "920186"]

    assert MODULE.filter_intraday_history_symbols(symbols, "eastmoney") == symbols
