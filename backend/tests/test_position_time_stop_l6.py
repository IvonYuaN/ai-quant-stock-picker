"""L6 时间止损：持有交易日数达到阈值即产「该卖」提醒。

依据（2026-11-09 组合级模拟，648 笔/3y）：L1=3%+L5=8% 无时间止损年化 15.4%，
加入 20 日时间止损后 24.4%、Sharpe 0.99。L6 为此新增的 opt-in 规则层。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "backend"))

import risk_rules  # noqa: E402
from aqsp.risk.live_risk import run_live_risk_check  # noqa: E402


def _holding(code="600000", cost=10.0):
    return [{"code": code, "shares": 100, "cost": cost, "position_id": "p1"}]


def _l6_rules(code="600000", days=20, enabled=True):
    return [{
        "id": f"{code}-L6", "position_id": "p1", "symbol": code, "level": "L6",
        "enabled": enabled, "param_value": days, "trigger_price": None,
        "freq_mode": "once", "origin": "AUTO_DEFAULT", "revision": 0,
    }]


def test_l6_fires_when_holding_days_reach_threshold():
    reminders = run_live_risk_check(
        _holding(), {"600000": 10.5}, holding_days={"600000": 20},
        position_rules=_l6_rules(days=20),
    )
    assert [r.level for r in reminders] == ["L6"]
    assert reminders[0].action_hint == "该卖"
    assert "时间止损" in reminders[0].basis["evidence"]


def test_l6_does_not_fire_before_threshold():
    reminders = run_live_risk_check(
        _holding(), {"600000": 10.5}, holding_days={"600000": 19},
        position_rules=_l6_rules(days=20),
    )
    assert reminders == []


def test_l6_respects_disabled_and_t1():
    assert run_live_risk_check(
        _holding(), {"600000": 10.5}, holding_days={"600000": 30},
        position_rules=_l6_rules(days=20, enabled=False),
    ) == []
    # 当日买入（holding_days=0）不催卖，也不应触发时间止损
    assert run_live_risk_check(
        _holding(), {"600000": 10.5}, holding_days={"600000": 0},
        position_rules=_l6_rules(days=5),
    ) == []


def test_ensure_rules_creates_l6_default_20_days():
    data = {"holdings": [{"code": "600000", "shares": 100, "cost": 10.0}]}
    rules = risk_rules.ensure_rules(data)
    l6 = [r for r in rules if r["level"] == "L6"]
    assert len(l6) == 1 and l6[0]["param_value"] == 20 and l6[0]["enabled"] is True


def test_patch_rule_validates_l6_window():
    rule = {"id": "600000-L6", "level": "L6", "param_value": 20, "enabled": True}
    risk_rules.patch_rule(rule, {"param_value": 10})
    assert rule["param_value"] == 10
    try:
        risk_rules.patch_rule(rule, {"param_value": 7})
    except ValueError as exc:
        assert "时间止损" in str(exc)
    else:
        raise AssertionError("非法天数应被拒绝")


def test_default_rules_enable_only_evidence_layers():
    """默认只开 L1/L5/L6；L2/L4 为模拟负贡献，默认关闭但可手工开启。"""
    data = {"holdings": [{"code": "600000", "shares": 100, "cost": 10.0}]}
    rules = risk_rules.ensure_rules(data)
    enabled = {r["level"]: r["enabled"] for r in rules}
    assert enabled == {"L1": True, "L2": False, "L4": False, "L5": True, "L6": True}
