from __future__ import annotations

import tempfile
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


def test_write_daily_research_report_wires_inputs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT", raising=False)
    monkeypatch.delenv("AQSP_DAILY_RESEARCH_REPORT", raising=False)
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


def test_write_daily_research_report_empty_picks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT", raising=False)
    monkeypatch.delenv("AQSP_DAILY_RESEARCH_REPORT", raising=False)
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


def test_write_daily_research_report_persists_outside_temp_report(monkeypatch) -> None:
    """PR #70 回归：scheduled 运行把 --report 指向临时目录时，v2 日报必须落到
    持久位置（运行时数据 reports/），而非随临时目录被 trap 清理删除。

    复现真实 cron 链路：intraday_refresh.sh 传 --report=${TMP_DIR}/intraday_latest.md
    （TMP_DIR 为 mktemp，退出即删），而 AQSP_RUNTIME_DATA_ROOT 指向持久数据根。
    """
    monkeypatch.delenv("AQSP_DAILY_RESEARCH_REPORT", raising=False)
    # 持久数据根（模拟 /opt/aqsp/data）
    runtime_root = tempfile.mkdtemp(prefix="aqsp_runtime_")
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", runtime_root)
    # 临时 report 目录（模拟 intraday_refresh.sh 的 mktemp TMP_DIR）
    temp_report_dir = tempfile.mkdtemp(prefix="aqsp_tmp_report_")
    args = Namespace(report=str(Path(temp_report_dir) / "intraday_latest.md"))

    picks = [
        _FakePick("600519", 0.9, "消费"),
        _FakePick("300750", 0.7, "新能源"),
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
    # 关键断言：日报不能落在临时 report 目录（否则会被清理删除）
    assert Path(path).parent != Path(temp_report_dir)
    # 必须落在持久数据根的 reports/ 下
    expected = Path(runtime_root) / "reports" / "daily_report.md"
    assert Path(path) == expected
    assert expected.is_file()
    # 模拟脚本退出时清理临时目录，持久日报不受影响
    import shutil

    shutil.rmtree(temp_report_dir, ignore_errors=True)
    assert expected.is_file(), "临时目录清理后，持久日报应仍在"
    content = expected.read_text(encoding="utf-8")
    assert "600519" in content
    assert "上行" in content
