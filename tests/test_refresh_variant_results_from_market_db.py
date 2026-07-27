from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "refresh_variant_results_from_market_db.py"
spec = importlib.util.spec_from_file_location(
    "refresh_variant_results_from_market_db", SCRIPT
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_market_symbol_filter_keeps_main_and_chinext_when_non_st() -> None:
    assert mod.is_supported_symbol("000001.SZ", "平安银行")
    assert mod.is_supported_symbol("300750.SZ", "宁德时代")
    assert mod.is_supported_symbol("600519.SH", "贵州茅台")
    assert mod.is_supported_symbol("605499.SH", "东鹏饮料")


def test_market_symbol_filter_drops_st_delisted_star_board_and_b_share() -> None:
    assert not mod.is_supported_symbol("688001.SH", "华兴源创")
    assert not mod.is_supported_symbol("000005.SZ", "ST星源")
    assert not mod.is_supported_symbol("000004.SZ", "国华退")
    assert not mod.is_supported_symbol("200001.SZ", "深物业B")


def test_balanced_symbols_interleaves_boards_when_capped() -> None:
    symbols = (
        mod.MarketSymbol("000001.SZ", "000001", "A", "深市主板"),
        mod.MarketSymbol("000002.SZ", "000002", "B", "深市主板"),
        mod.MarketSymbol("300001.SZ", "300001", "C", "创业板"),
        mod.MarketSymbol("300002.SZ", "300002", "D", "创业板"),
        mod.MarketSymbol("600001.SH", "600001", "E", "沪市主板"),
        mod.MarketSymbol("600002.SH", "600002", "F", "沪市主板"),
    )
    picked = mod.balanced_symbols(symbols, 3)
    assert [item.group for item in picked] == ["深市主板", "创业板", "沪市主板"]
