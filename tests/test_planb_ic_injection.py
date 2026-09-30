"""planb IC 侧注入（方案 B 主判据「改后权重双窗 IC 对照」入口）单测。

裁决单 §三步骤 2 的可执行入口：触发日跑
  python scripts/ic_dual_verdict.py ... --planb-profile planb_v1 --output-dir <独立目录>
即让 composite/基础三因子按 planb 候选 7 维权重打分，**不改 thresholds.yaml**，
与生产 WF001 口径的滚动监控（默认路径，cron 零漂移）天然隔离。

合成库复用 test_ic_dual_verdict._make_db（n_days=1160，够双窗 2×40+lookback）。
断言重点是「注入路径生效」= ic_profile 字段 + composite 因子在 factors + report 标注，
不依赖合成数据能否给 quality/value 打分（无财务数据 ⇒ 可能退化为 0/NaN，属预期）。
"""

from __future__ import annotations

import json

import pytest

from scripts.analysis import ic_diagnosis
from scripts.analysis.ic_dual_verdict import run_dual_verdict
from tests.test_ic_dual_verdict import _make_db


class TestPlanbProfileVariant:
    def test_planb_profiles_resolve_to_representative_arm(self):
        from aqsp.cli import _PLANB_VARIANTS

        for prof in ("planb_v1", "planb_v2", "planb_v3"):
            variant = ic_diagnosis._planb_profile_variant(prof)
            # 代表臂 = 该 profile 第一臂，strategy_mix 必须 planb，7 维齐
            assert variant.strategy_mix == "planb"
            assert variant is _PLANB_VARIANTS[prof][0]
            assert variant.composite_weights is not None and len(variant.composite_weights) == 7

    def test_unknown_profile_rejected(self):
        with pytest.raises(ValueError):
            ic_diagnosis._planb_profile_variant("nope")


class TestRunPlanbInjection:
    def test_run_default_is_wf001_zero_drift(self, tmp_path):
        db = _make_db(tmp_path / "p.db")
        out = tmp_path / "ic"
        res = ic_diagnosis.run(
            db, window_days=40, lookback=60, horizon=3, step=5,
            out_dir=str(out), write_ready=False,
        )
        assert res["ic_profile"] == "wf001"
        assert "composite" in res["factors"]

    @pytest.mark.parametrize("prof", ["planb_v1", "planb_v2", "planb_v3"])
    def test_run_planb_injection_sets_ic_profile_and_keeps_composite(self, tmp_path, prof):
        db = _make_db(tmp_path / "p.db")
        out = tmp_path / "ic_planb"
        res = ic_diagnosis.run(
            db,
            window_days=40,
            lookback=60,
            horizon=3,
            step=5,
            extra_factors=["htf", "volume", "mr"],
            out_dir=str(out),
            write_ready=False,
            planb_profile=prof,
        )
        # 注入来源可观测（proposal-only，不写回打分/排序/下单）
        assert res["ic_profile"] == prof
        # 注入后 composite/基础三因子仍在（7 维权重经 _apply_walkforward_grid_variant 落到
        # CompositeStrategy；extra htf 随 planb htf 权重>0 而 enabled）
        assert "composite" in res["factors"]
        assert "momentum" in res["factors"] and "triple_rise" in res["factors"]
        # 注入 profile 的额外因子应并入打分集（htf 权重>0 ⇒ enabled）
        assert "htf" in res["factors"]
        # 产物 JSON 同构
        latest = json.loads((out / "factor_ic_latest.json").read_text())
        assert latest["ic_profile"] == prof


class TestDualVerdictPlanbInjection:
    def test_dual_verdict_default_is_wf001(self, tmp_path):
        db = _make_db(tmp_path / "p.db")
        res = run_dual_verdict(
            db, out_dir=str(tmp_path / "dual_wf"), window_days=40, step=5,
            extra_factors=["mr"], write_ready=False,
        )
        assert res["ic_profile"] == "wf001"
        report = (tmp_path / "dual_wf" / "dual_report.md").read_text()
        assert "生产 WF001 口径" in report

    def test_dual_verdict_planb_sets_ic_profile_and_report_marker(self, tmp_path):
        db = _make_db(tmp_path / "p.db")
        out = tmp_path / "dual_planb_v3"
        res = run_dual_verdict(
            db,
            out_dir=str(out),
            window_days=40,
            step=5,
            extra_factors=["mr", "htf"],
            write_ready=False,
            planb_profile="planb_v3",
        )
        assert res["ic_profile"] == "planb_v3"
        # report 标注注入口径（与生产 WF001 可区分）
        report = (out / "dual_report.md").read_text()
        assert "planb_v3" in report
        assert "planb 注入跑批" in report
        # latest.json 同构
        latest = json.loads((out / "dual_window_latest.json").read_text())
        assert latest["ic_profile"] == "planb_v3"
        # 🔴 ledger 行**不**带 ic_profile（保持生产 ledger 结构稳定；注入走独立 out_dir）
        row = json.loads((out / "dual_window_history.jsonl").read_text().splitlines()[-1])
        assert "ic_profile" not in row

    def test_dual_verdict_unknown_profile_rejected(self, tmp_path):
        db = _make_db(tmp_path / "p.db")
        with pytest.raises(ValueError):
            run_dual_verdict(
                db, out_dir=str(tmp_path / "x"), window_days=40, step=5,
                planb_profile="bogus",
            )
