"""`aqsp market-risk` 子命令的行为测试。

🔴 核心约束有两条，必须锁住：
1. 输出必须带「仅作风险提示，不参与选股打分」——防止有人把这条命令
   接进打分或当作择时 alpha（2026-11-12 实测：叠加趋势曝光后 walk-forward
   均值 Sharpe 由 +0.66 降到 +0.53，只有 MDD 改善）。
2. 库缺失 / 样本不足必须**非 0 退出码 + stderr**，不得静默输出空结果，
   否则 cron 会把"算不出来"当成"没有风险"。
"""

from __future__ import annotations

import argparse
import json

from aqsp.market_risk import MarketRiskSnapshot


def _snap() -> MarketRiskSnapshot:
    return MarketRiskSnapshot(
        as_of="20261106",
        trend_20=-0.0997,
        trend_60=-0.1522,
        breadth=0.337,
        drawdown_252=-0.4980,
        level="high",
        reasons=("20 日趋势向下（-10.0%）", "距一年高点回撤 -50%"),
        n_symbols=1107,
    )


def test_text_output_contains_risk_disclaimer(monkeypatch, capsys) -> None:
    from aqsp import cli

    monkeypatch.setattr("aqsp.data.source_factory.resolve_sqlite_db_path", lambda: "/tmp/x.db")
    monkeypatch.setattr("aqsp.market_risk.compute_market_risk", lambda db, day: _snap())

    rc = cli.run_market_risk(argparse.Namespace(as_of="20261106", json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "high" in out
    assert "仅作风险提示" in out, "必须显式声明用途，防止被当 alpha 用"


def test_json_output_marks_usage(monkeypatch, capsys) -> None:
    from aqsp import cli

    monkeypatch.setattr("aqsp.data.source_factory.resolve_sqlite_db_path", lambda: "/tmp/x.db")
    monkeypatch.setattr("aqsp.market_risk.compute_market_risk", lambda db, day: _snap())

    rc = cli.run_market_risk(argparse.Namespace(as_of="20261106", json=True))
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert payload["usage"] == "risk_warning_only"
    assert payload["level"] == "high"


def test_missing_db_exits_nonzero(monkeypatch, capsys) -> None:
    from aqsp import cli

    monkeypatch.setattr("aqsp.data.source_factory.resolve_sqlite_db_path", lambda: None)
    rc = cli.run_market_risk(argparse.Namespace(as_of="", json=False))
    assert rc != 0
    assert "未找到本机日线库" in capsys.readouterr().err


def test_insufficient_sample_exits_nonzero(monkeypatch, capsys) -> None:
    from aqsp import cli

    monkeypatch.setattr("aqsp.data.source_factory.resolve_sqlite_db_path", lambda: "/tmp/x.db")
    monkeypatch.setattr("aqsp.market_risk.compute_market_risk", lambda db, day: None)
    rc = cli.run_market_risk(argparse.Namespace(as_of="20260101", json=False))
    assert rc != 0
    assert "样本不足" in capsys.readouterr().err
