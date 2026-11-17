"""估值 PIT（PE/PB/PS/PCF/总市值）测试。

核心红线：
- **asof 语义严格**：只返回 ``trade_date <= as_of`` 的最近一期快照；
  早于全部快照 ⇒ None；**绝不返回未来期**（这是无前视的根本保证）。
- 快照是**定期快照**（每 14 天一期）⇒ asof 取"不晚于该日可见的最近一期"。
- 锁死 #335 两项：缓存落 pit_cache、``load_cached()`` 只读预热（绝不联网）。
- 缺字段/坏值 ⇒ None（fail-soft），不抛异常、不返回 0。
"""

from __future__ import annotations

import os

import pandas as pd

from aqsp.data.valuation_pit import (
    ValuationPitSource,
    ValuationPoint,
    _coerce_code,
    _to_float,
)


def _points() -> list[ValuationPoint]:
    return [
        ValuationPoint("000001", "20240110", pe_ttm=8.0, pb_mrq=0.9, ps_ttm=1.2, pcf_ocf_ttm=7.0, total_market_cap=1.0e11),
        ValuationPoint("000001", "20240124", pe_ttm=7.5, pb_mrq=0.85, ps_ttm=1.1, pcf_ocf_ttm=6.8, total_market_cap=1.1e11),
        ValuationPoint("000001", "20240207", pe_ttm=9.9, pb_mrq=1.0, ps_ttm=1.3, pcf_ocf_ttm=7.2, total_market_cap=1.2e11),
        ValuationPoint("000002", "20240124", pe_ttm=20.0, pb_mrq=3.0, ps_ttm=4.0, pcf_ocf_ttm=15.0, total_market_cap=5.0e10),
    ]


# --------------------------------------------------------------------- asof 语义
def test_lookup_takes_latest_snapshot_not_after_asof():
    src = ValuationPitSource().from_points(_points())
    # 20240115 ⇒ 落在 20240110 与 20240124 之间 ⇒ 取 20240110（**不是** 20240124）
    p = src.lookup("000001", "20240115")
    assert p is not None
    assert p.trade_date == "20240110"
    assert p.pe_ttm == 8.0


def test_lookup_exact_snapshot_date_is_inclusive():
    src = ValuationPitSource().from_points(_points())
    p = src.lookup("000001", "20240124")
    assert p is not None
    assert p.trade_date == "20240124"
    assert p.pe_ttm == 7.5


def test_lookup_before_all_snapshots_returns_none():
    src = ValuationPitSource().from_points(_points())
    assert src.lookup("000001", "20231231") is None
    assert src.lookup("000001", "20240109") is None


def test_lookup_never_returns_future_snapshot():
    """🔴 核心红线：asof 之后才发布的快照绝不能被取到。"""
    src = ValuationPitSource().from_points(_points())
    # 20240207 的快照在 asof=20240120 时不可见
    p = src.lookup("000001", "20240120")
    assert p is not None
    assert p.trade_date == "20240110"
    assert p.pe_ttm != 9.9  # 未来期值绝不出现


def test_lookup_unknown_code_returns_none():
    src = ValuationPitSource().from_points(_points())
    assert src.lookup("999999", "20240124") is None


def test_lookup_supports_dashed_and_compact_asof():
    src = ValuationPitSource().from_points(_points())
    a = src.lookup("000001", "2024-01-24")
    b = src.lookup("000001", "20240124")
    assert a is not None and b is not None
    assert a.trade_date == b.trade_date


def test_lookup_invalid_asof_returns_none():
    src = ValuationPitSource().from_points(_points())
    assert src.lookup("000001", "not-a-date") is None
    assert src.lookup("000001", "2024") is None


# --------------------------------------------------------------------- 便捷访问
def test_convenience_accessors_return_snapshot_values():
    src = ValuationPitSource().from_points(_points())
    assert src.pe("000001", "20240124") == 7.5
    assert src.pb("000001", "20240124") == 0.85
    assert src.total_market_cap("000001", "20240124") == 1.1e11
    # 无数据 ⇒ None（fail-soft）
    assert src.pe("999999", "20240124") is None


def test_second_code_has_own_timeline():
    src = ValuationPitSource().from_points(_points())
    p = src.lookup("000002", "20240124")
    assert p is not None and p.pe_ttm == 20.0
    # 000002 在 20240207 无快照 ⇒ 回退到自己的最近一期
    p2 = src.lookup("000002", "20240210")
    assert p2 is not None and p2.trade_date == "20240124"


# --------------------------------------------------------------------- 字段清洗
def test_to_float_handles_bad_values():
    assert _to_float("12.5") == 12.5
    assert _to_float(None) is None
    assert _to_float("") is None
    assert _to_float("abc") is None
    assert _to_float(float("nan")) is None


def test_coerce_code_pads_to_six_digits():
    assert _coerce_code("1") == "000001"
    assert _coerce_code("000001.SZ") == "000001.SZ"  # 不改动已带后缀的输入
    assert _coerce_code(1) == "000001"
    assert _coerce_code("2.0") == "000002"


# --------------------------------------------------------------------- 缓存（#335）
def test_load_cached_is_read_only_and_missing_cache_is_soft(tmp_path):
    """缓存缺失 ⇒ load_cached() 返回 False 且不抛异常（绝不联网）。"""
    src = ValuationPitSource(cache_dir=str(tmp_path))
    assert src.load_cached() is False
    assert src.lookup("000001", "20240124") is None


def test_load_cached_reads_local_csv(tmp_path):
    path = tmp_path / "valuation_history.csv"
    pd.DataFrame(
        [
            {
                "SECURITY_CODE": "000001",
                "TRADE_DATE": "20240124",
                "PE_TTM": 7.5,
                "PB_MRQ": 0.85,
                "PS_TTM": 1.1,
                "PCF_OCF_TTM": 6.8,
                "TOTAL_MARKET_CAP": 1.1e11,
            },
            {
                "SECURITY_CODE": "000001",
                "TRADE_DATE": "20240207",
                "PE_TTM": 9.9,
                "PB_MRQ": 1.0,
                "PS_TTM": 1.3,
                "PCF_OCF_TTM": 7.2,
                "TOTAL_MARKET_CAP": 1.2e11,
            },
        ]
    ).to_csv(path, index=False)
    src = ValuationPitSource(cache_dir=str(tmp_path))
    assert src.load_cached() is True
    # asof 20240124 ⇒ 取当期；asof 20240210 ⇒ 取下一期
    assert src.pe("000001", "20240124") == 7.5
    assert src.pe("000001", "20240210") == 9.9


def test_default_cache_path_lands_in_pit_cache():
    src = ValuationPitSource()
    path = src._default_cache_path()
    assert path.endswith(os.path.join("pit_cache", "valuation_history.csv"))
