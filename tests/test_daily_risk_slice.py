"""路线② CLI（scripts/research/daily_risk_slice.py）端到端测试。

红线：纯计算 / 只读 / 确定性 / fail-soft；接在路线①筛选器快照之后做风险切片，
**不牵动生产下单链、不联网**。用**合成快照 dict**（不碰 2.6GB 库、不跨测试文件
import），验证「①的 JSON → CLI → 切片 JSON/MD」全链。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "research" / "daily_risk_slice.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("daily_risk_slice_under_test", str(_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _screener_snapshot(tmp_path: Path, top: int = 6) -> Path:
    """造一份路线①风格的筛选器快照（build_screener_snapshot 的输出形状）。"""
    snap = {
        "as_of": "20240131",
        "regime": "unknown",
        "thresholds_version": "1.1.21",
        "universe_count": top * 2,
        "selected_count": top,
        "selected": [
            {"symbol": f"{i:06d}.SZ", "total_score": round(0.9 - i * 0.05, 4), "rank": i + 1}
            for i in range(top)
        ],
        "notes": ["synthetic"],
        "snapshot_hash": "sha256:0000",
    }
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return p


def test_cli_end_to_end_json_and_md(tmp_path):
    mod = _load_module()
    snap = _screener_snapshot(tmp_path, top=6)
    out_json = tmp_path / "slice.json"
    out_md = tmp_path / "slice.md"
    rc = mod.main([
        "--from-json", str(snap),
        "--market-caps", str(tmp_path / "none_caps.json"),  # 不存在 ⇒ 走中性
        "--top-n", "5",
        "--max-single-weight", "0.30",
        "--max-sector-weight", "0.40",
        "--min-cash", "0.10",
        "--out-json", str(out_json),
        "--out-md", str(out_md),
    ])
    assert rc == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    # top_n=5 ⇒ holdings 截到 5
    assert data["n_holdings"] == 5
    assert 0.0 <= data["cash"] <= 1.0
    assert data["invested"] == pytest.approx(1.0 - data["cash"], abs=1e-3)
    assert sum(data["weights"].values()) <= 0.90 + 1e-3
    assert set(data) >= {
        "as_of", "n_holdings", "cash", "invested", "weights",
        "sector_exposure", "cap_tier_exposure", "risk_flags", "notes", "config",
    }
    assert out_md.exists()
    md = out_md.read_text(encoding="utf-8")
    assert "组合风险切片" in md
    # 不塞下单逻辑
    for w in ("买入", "卖出", "加仓", "清仓", "止损"):
        assert w not in md


def test_cli_missing_snapshot_returns_2(tmp_path):
    mod = _load_module()
    rc = mod.main(["--from-json", str(tmp_path / "nope.json")])
    assert rc == 2


def test_cli_fills_maps_and_keeps_neutral_when_missing(tmp_path):
    mod = _load_module()
    snap = _screener_snapshot(tmp_path, top=3)
    # 只给第一只票市值/波动/换手/行业，其余全缺 ⇒ 其余走中性
    caps = tmp_path / "caps.json"
    caps.write_text(json.dumps({"000000.SZ": 25e8, "000001.SZ": 300e8}), encoding="utf-8")
    vols = tmp_path / "vols.json"
    vols.write_text(json.dumps({"000000.SZ": 0.18, "000001.SZ": 0.55}), encoding="utf-8")
    sec = tmp_path / "sec.json"
    sec.write_text(json.dumps({"000000.SZ": "白酒", "000001.SZ": "医药"}), encoding="utf-8")
    out_json = tmp_path / "s.json"
    rc = mod.main([
        "--from-json", str(snap),
        "--market-caps", str(caps),
        "--annual-vols", str(vols),
        "--sectors", str(sec),
        "--low-vol-tilt", "0.5",
        "--out-json", str(out_json),
    ])
    assert rc == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    # 有 3 只票，行业映射只覆盖 2 只 ⇒ notes 应标注缺行业
    assert data["n_holdings"] == 3
    assert any("缺" in n for n in data["notes"])


def test_cli_deterministic_rerun(tmp_path):
    mod = _load_module()
    snap = _screener_snapshot(tmp_path, top=6)
    caps = tmp_path / "caps.json"
    caps.write_text(json.dumps({f"{i:06d}.SZ": 100e8 + i * 1e8 for i in range(6)}), encoding="utf-8")
    j1, j2 = tmp_path / "a.json", tmp_path / "b.json"
    mod.main(["--from-json", str(snap), "--market-caps", str(caps), "--out-json", str(j1)])
    mod.main(["--from-json", str(snap), "--market-caps", str(caps), "--out-json", str(j2)])
    assert json.loads(j1.read_text()) == json.loads(j2.read_text())
