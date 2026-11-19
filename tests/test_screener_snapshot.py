"""筛选器产品化核心（screener.build_screener_snapshot）测试。

红线：确定性（重跑逐位一致）/ PIT 安全（只用入参 data，绝不取未来）/
fail-soft（空数据不崩、不造假）/ 单一事实源（复用 CompositeStrategy 生产口径）。
全部用**合成数据**（不碰 2.6GB 库、不联网、可复跑）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aqsp.strategies.screener import build_screener_snapshot, render_markdown
from aqsp.strategies.thresholds import load_thresholds


def _frames(n: int = 6, horizon: int = 30, seed: int = 7) -> dict[str, pd.DataFrame]:
    """n 只票 × 30 日合成日线（close/volume），让 momentum/price_volume_corr 可算。"""
    rng = np.random.default_rng(seed)
    data: dict[str, pd.DataFrame] = {}
    for i in range(n):
        close = 10 + np.cumsum(rng.normal(0, 0.1, horizon)) + i * 0.5
        volume = rng.uniform(5e6, 2e7, horizon)
        data[f"{i:06d}.SZ"] = pd.DataFrame(
            {
                "date": [pd.Timestamp("2024-01-02") + pd.Timedelta(days=k) for k in range(horizon)],
                "close": close,
                "open": close * 0.99,
                "high": close * 1.02,
                "low": close * 0.98,
                "volume": volume,
                "amount": volume * close,
            }
        )
    return data


def test_snapshot_deterministic_on_rerun():
    """🔴 核心契约：同 data + regime + thresholds ⇒ 输出逐位一致（snapshot_hash 相同）。"""
    th = load_thresholds()
    data = _frames()
    a = build_screener_snapshot(data, regime="unknown", thresholds=th, top_n=5, as_of="20240131")
    b = build_screener_snapshot(data, regime="unknown", thresholds=th, top_n=5, as_of="20240131")
    assert a["snapshot_hash"] == b["snapshot_hash"]
    assert a["selected"] == b["selected"]


def test_snapshot_uses_only_provided_data_no_future():
    """PIT：只吃入参 data（调用方保证 date<=as_of），本模块绝不自己取数。

    给一组数据，断言 selected 里的 symbol ⊆ 入参 universe（不冒出来历不明的票）。
    """
    th = load_thresholds()
    data = _frames(n=4)
    snap = build_screener_snapshot(data, regime="unknown", thresholds=th, top_n=3, as_of="20240131")
    for e in snap["selected"]:
        assert e["symbol"] in data, "选出的票必须来自入参 universe（PIT 边界）"


def test_empty_data_is_failsoft():
    """空 universe ⇒ 空清单、不崩、不造假信号。"""
    th = load_thresholds()
    snap = build_screener_snapshot({}, regime="unknown", thresholds=th, top_n=5, as_of="20240131")
    assert snap["selected"] == []
    assert snap["selected_count"] == 0
    assert snap["snapshot_hash"].startswith("sha256:")


def test_all_empty_frames_is_failsoft():
    """universe 有 key 但全空 DataFrame ⇒ 同样 fail-soft。"""
    th = load_thresholds()
    snap = build_screener_snapshot({"000001.SZ": pd.DataFrame()}, regime="unknown", thresholds=th, top_n=5)
    assert snap["selected"] == []
    assert snap["universe_count"] == 0


def test_selected_fields_complete():
    """每只入选票字段完整（rank/total/dim_scores/reasons/risk_flags）。"""
    th = load_thresholds()
    data = _frames(n=12)
    snap = build_screener_snapshot(data, regime="unknown", thresholds=th, top_n=6, as_of="20240131")
    assert snap["universe_count"] == 12
    for e in snap["selected"]:
        assert set(e) >= {"symbol", "rank", "total_score", "dim_scores", "reasons", "risk_flags"}
        assert isinstance(e["reasons"], list) and len(e["reasons"]) >= 1
        assert 0.0 <= e["total_score"] <= 1.0


def test_top_n_respected_and_ranking_desc():
    """清单长度 ≤ top_n；按 total 降序（同分按 symbol 稳定）。"""
    th = load_thresholds()
    data = _frames(n=12)
    snap = build_screener_snapshot(data, regime="unknown", thresholds=th, top_n=4, as_of="20240131")
    assert len(snap["selected"]) <= 4
    totals = [e["total_score"] for e in snap["selected"]]
    assert totals == sorted(totals, reverse=True)
    ranks = [e["rank"] for e in snap["selected"]]
    assert ranks == list(range(1, len(snap["selected"]) + 1))


def test_render_markdown_smoke():
    """Markdown 渲染不崩、含关键表头与 as_of。"""
    th = load_thresholds()
    data = _frames(n=6)
    snap = build_screener_snapshot(data, regime="unknown", thresholds=th, top_n=3, as_of="20240131")
    md = render_markdown(snap, title="测试清单")
    assert "# 测试清单" in md
    assert "20240131" in md
    assert "代码" in md and "总分" in md
    # 无指令性词（筛选工具不塞下单逻辑）
    for word in ("买入", "卖出", "加仓", "清仓"):
        assert word not in md
