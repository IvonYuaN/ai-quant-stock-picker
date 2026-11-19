"""短线影子账户只读桥接的契约测试。

重点不是"能读出数字"，而是**该说没有的时候必须说没有**：
产物缺失 / 损坏 / schema 不匹配 / 过期，都必须 available=False 并给原因。
一旦它悄悄返回 0，前端就会把"没有数据"画成"收益为零"——这是最典型的用数字骗自己。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import paper_arena_bridge as pb  # noqa: E402


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _good(**over) -> dict:
    base = {
        "schema_version": "v1",
        "generated_at": "2026-11-20T15:12:00+08:00",
        "as_of": "2026-11-20",
        "initial_capital": 10000,
        "total_capital": 50000,
        "total_nav": 50310.5,
        "total_ret_pct": 0.62,
        "trading_days": 3,
        "snapshot_days": 4,
        "cost_round_trip_pct": 2,
        "sleeves": [{
            "id": "P3", "name": "超跌反抽", "hypothesis": "短期反转",
            "n": 42, "days": 3, "exc_mean": 0.85, "exc_win": 57.1,
            "t_exc": 1.92, "q_exc": 0.11, "n_need_days": 12,
            "funded_nav": 10050.0, "funded_ret": 0.5, "funded_n": 12,
            "funded_win": 58.3, "positions": 2, "sealed_n": 0, "unfunded": {"仓位已满": 30},
        }],
        "nav": {"dates": ["2026-11-18", "2026-11-19", "2026-11-20"],
                "port": [0.0, -1.35, 0.62], "bench": [0.0, -0.91, -0.2]},
        "positions": [{"sleeve": "P3", "code": "600001", "name": "票", "buy_date": "2026-11-20",
                       "buy_price": 10.0, "shares": 100, "note": "昨日-7.5%"}],
        "recent_trades": [{"sleeve": "P3", "code": "600002", "name": "票2",
                           "sell_date": "2026-11-20", "ret_pct": 1.2,
                           "excess_pct": 1.4, "reason": "T+1收盘兑现", "funded": True}],
    }
    base.update(over)
    return base


@pytest.fixture()
def arena(tmp_path, monkeypatch):
    path = tmp_path / "paper_arena.json"
    monkeypatch.setenv("AQSP_PAPER_ARENA", str(path))
    return path


def test_missing_file_is_unavailable_not_zero(arena):
    out = pb.payload()
    assert out["available"] is False
    assert "尚未收到" in out["reason"]
    # 关键：不能把"没有数据"伪装成"收益 0"
    assert out["sleeves"] == []
    assert out["nav"]["dates"] == []


def test_corrupt_file_degrades(arena):
    arena.write_text("{not json", encoding="utf-8")
    out = pb.payload()
    assert out["available"] is False
    assert "读取失败" in out["reason"]


def test_schema_mismatch_degrades(arena):
    _write(arena, _good(schema_version="v0"))
    out = pb.payload()
    assert out["available"] is False
    assert "schema" in out["reason"]


def test_good_payload_passes_through_verbatim(arena):
    _write(arena, _good())
    out = pb.payload()
    assert out["available"] is True
    assert out["stale"] is False
    row = out["sleeves"][0]
    # 数字必须原样带出：桥接不做二次统计，否则前端和账本会分叉
    assert row["exc_mean"] == 0.85
    assert row["t_exc"] == 1.92
    assert row["q_exc"] == 0.11
    assert row["n_need_days"] == 12
    assert out["nav"]["port"][-1] == 0.62
    assert out["positions"][0]["code"] == "600001"


def test_null_stats_stay_null_not_zero(arena):
    """还没样本时 exc_mean/t_exc 是 null，桥接必须保持 null。"""
    _write(arena, _good(sleeves=[{"id": "P1", "name": "二板接力", "hypothesis": "",
                                  "n": 0, "days": 0, "exc_mean": None, "t_exc": None,
                                  "q_exc": None, "n_need_days": None}]))
    row = pb.payload()["sleeves"][0]
    assert row["exc_mean"] is None
    assert row["t_exc"] is None
    assert row["q_exc"] is None
    assert row["n_need_days"] is None


def test_stale_flag_when_old(arena):
    _write(arena, _good(generated_at="2020-01-01T00:00:00+08:00"))
    assert pb.payload()["stale"] is True


def test_nav_series_truncated_and_aligned(arena):
    dates = ["2026-01-%02d" % d for d in range(1, 32)] * 5
    _write(arena, _good(nav={"dates": dates, "port": [0.1] * len(dates),
                             "bench": [0.0] * len(dates)}))
    nav = pb.payload()["nav"]
    assert len(nav["dates"]) == 120
    assert len(nav["port"]) == 120
    assert len(nav["bench"]) == 120
