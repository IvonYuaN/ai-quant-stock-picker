from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from aqsp.cli import _write_daily_research_report


class _FakePick:
    """最小可运行的筛选标的桩，仅暴露 helper 需要的属性。"""

    def __init__(self, symbol: str, score: float, sector: str) -> None:
        self.symbol = symbol
        self.score = score
        self.metrics = {"sector": sector}


def _fake_strategy_perf() -> dict:
    return {
        "momentum": SimpleNamespace(
            recent_performance=SimpleNamespace(
                win_rate=0.6,
                avg_return=0.02,
                max_drawdown=0.1,
                sharpe_ratio=1.2,
            ),
            weights={"base": 1.1},
        )
    }


def test_write_daily_research_report_wires_inputs(tmp_path: Path) -> None:
    args = Namespace(report=str(tmp_path / "latest.md"))
    picks = [
        _FakePick("600519", 0.9, "消费"),
        _FakePick("300750", 0.7, "新能源"),
        _FakePick("000001", 0.5, "金融"),
    ]
    regime = SimpleNamespace(name="bull", confidence=0.8, description="上行")
    breaker = SimpleNamespace(triggered=False, reason="正常")

    path = _write_daily_research_report(
        args=args,
        strategy_performances=_fake_strategy_perf(),
        picks=picks,
        regime=regime,
        breaker_status=breaker,
    )

    assert path is not None
    content = Path(path).read_text(encoding="utf-8")
    assert "# AI量化选股研究日报" in content
    assert "600519" in content
    assert "消费" in content
    assert "momentum" in content
    assert "上行" in content
    # 落盘路径应为 --report 同级目录下的 daily_report.md
    assert Path(path).name == "daily_report.md"
    assert Path(path).parent == tmp_path


def test_write_daily_research_report_empty_picks(tmp_path: Path) -> None:
    args = Namespace(report=str(tmp_path / "latest.md"))
    regime = SimpleNamespace(name="unknown", confidence=0.0, description="无法判定")
    breaker = SimpleNamespace(triggered=True, reason="回撤超限")

    path = _write_daily_research_report(
        args=args,
        strategy_performances={},
        picks=[],
        regime=regime,
        breaker_status=breaker,
    )

    assert path is not None
    content = Path(path).read_text(encoding="utf-8")
    assert "# AI量化选股研究日报" in content
    # 熔断触发状态应出现在报告里
    assert "回撤超限" in content
