"""守卫：`--streaming` 的 walkforward 跑批**必须真的产出 report.md**。

背景（2026-10-08 实测，P0）：
``run_walkforward`` 里 ``fetch_result`` 只在 ``if streaming_context is None:`` 分支内赋值，
而报告段（**同一函数的尾部**）无条件读 ``fetch_result.pit_note``。生产 gate cron 走的
正是 ``--streaming``（架构上强制 ``--skip-pit-financials``，见 cli.py:3691），该分支
永不执行 ⇒ 必然
``UnboundLocalError: local variable 'fetch_result' referenced before assignment``。

危害在于**崩点位置**：全部臂 + DSR/PBO 都算完、写 ``report.md`` 之前。
- lab：8 臂约 81min 算完后白费，``report.md`` 从未生成；
- prod：每周六 01:00 的门禁会跑满约 9.8h 后崩，DSR/PBO 全部作废、``RESULT_READY`` 不更新。

修法是 ``fetch_result: Any = None`` 预初始化 —— ``getattr(None, "pit_note", None)`` 得
``None``，自动落入既有的 ``skip_pit_financials`` 兜底分支，报告照常生成、行为零变化。

为何要新开文件：既有的 ``test_report_pit_skip_disclosure.py`` 只做**源码文本**断言，
抓不到运行时作用域错误；而本缺陷的形态就是「跑完才崩」，必须真跑一次才能守住。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import aqsp.cli as cli_mod


#: 报告里 PIT 跳过披露的固定开头（cli.py 报告段与 test_report_pit_skip_disclosure 同源）。
PIT_SKIP_MARKER = "本次跑批跳过 PIT 财务数据"


# --------------------------------------------------------------------------- #
# 跑批夹具
# --------------------------------------------------------------------------- #
def _parse_walkforward_args(monkeypatch: pytest.MonkeyPatch, *extra: str):
    """借真实 argparse 取一份完整 args（避免手搓 Namespace 随参数增删而漂移）。

    做法：把 ``run_walkforward`` 换成捕获器跑一次 ``main()``，拿到解析结果后再还原。
    """
    captured: dict[str, Any] = {}
    real = cli_mod.run_walkforward
    monkeypatch.setattr(
        cli_mod,
        "run_walkforward",
        lambda args: (captured.setdefault("args", args), 0)[1],
    )
    rc = cli_mod.main(list(extra))
    monkeypatch.setattr(cli_mod, "run_walkforward", real)
    assert rc == 0, "参数捕获阶段不应执行真正的 walkforward"
    return captured["args"]


def _stub_result() -> SimpleNamespace:
    """报告段会读到的全部 result 属性（多给无害，少给会 AttributeError）。"""
    overall = SimpleNamespace(
        total_return=0.05,
        annual_return=0.06,
        max_drawdown=-0.10,
        sharpe_ratio=1.20,
        win_rate=0.55,
        profit_factor=1.30,
        trades=40,
        not_executable=2,
        sharpe_ratio_active=1.20,
        win_rate_active=0.55,
    )
    period = SimpleNamespace(
        period="2024-01",
        total_return=0.01,
        sharpe_ratio=1.0,
        win_rate=0.5,
        trades=5,
        not_executable=0,
        skipped=False,
    )
    return SimpleNamespace(
        overall=overall,
        periods=[period],
        regime_winrates={},
        deflated_sharpe=1.5,
        pbo=0.3,
        robustness_score=0.8,
        parameter_std=0.01,
    )


class _StubEngine:
    """只实现 ``run`` / ``run_streaming`` —— 走到它们就够，不做真回测。"""

    def __init__(self, result: SimpleNamespace) -> None:
        self._result = result
        self.calls: list[str] = []

    def run(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        self.calls.append("run")
        return self._result

    def run_streaming(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        self.calls.append("run_streaming")
        return self._result


def _patch_engine(monkeypatch: pytest.MonkeyPatch, result: SimpleNamespace) -> _StubEngine:
    engine = _StubEngine(result)
    resolution = SimpleNamespace(
        requested="builtin", resolved="builtin", mode="builtin", message="stub"
    )
    monkeypatch.setattr(
        cli_mod, "resolve_walkforward_engine", lambda _name: (engine, resolution)
    )
    # 不写真实 gate sidecar（避免污染仓库路径）
    monkeypatch.setattr(cli_mod, "_write_walkforward_gate", lambda **_kwargs: None)
    return engine


# --------------------------------------------------------------------------- #
# 1) 回归：`--streaming` 跑批必须产出 report.md
# --------------------------------------------------------------------------- #
def test_streaming_walkforward_writes_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--streaming`` 跑完必须落地 report.md（2026-10-08 就是崩在这一步之前）。"""
    report = tmp_path / "report.md"
    args = _parse_walkforward_args(
        monkeypatch,
        "walkforward",
        "--streaming",
        "--engine",
        "builtin",
        "--source",
        "sqlite_db",
        "--skip-pit-financials",
        "--symbols",
        "600000,600001",
        "--start",
        "2024-01-02",
        "--end",
        "2024-12-31",
        "--report",
        str(report),
        "--gate-path",
        str(tmp_path / "gate.json"),
    )
    assert args.streaming is True, "本用例的前提是走 --streaming 分支"

    monkeypatch.setattr(
        cli_mod,
        "_build_streaming_sqlite_context",
        lambda _args, symbols: (symbols, lambda *_a, **_k: {}, [], {}, {}),
    )
    engine = _patch_engine(monkeypatch, _stub_result())

    rc = cli_mod.run_walkforward(args)

    assert rc == 0
    assert engine.calls == ["run_streaming"], "streaming 路径必须走 run_streaming"
    assert report.exists(), (
        "`--streaming` 跑批没有产出 report.md —— 典型原因是报告段读到了只在 "
        "`if streaming_context is None:` 分支里赋值的变量（如 fetch_result），"
        "在 streaming 路径下会抛 UnboundLocalError 并崩在写报告之前。"
    )
    assert "Walk-Forward 回测报告" in report.read_text(encoding="utf-8")


def test_streaming_report_keeps_pit_skip_disclosure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """预初始化不得吞掉披露：``fetch_result=None`` 时仍要落到 ``skip_pit_financials`` 兜底。

    ``--streaming`` 架构上强制跳过 PIT 财务 ⇒ quality/value/mean_reversion 三维恒为常数，
    报告必须如实标注。这条同时证明「预初始化」没有把披露一起吃掉。
    """
    report = tmp_path / "report.md"
    args = _parse_walkforward_args(
        monkeypatch,
        "walkforward",
        "--streaming",
        "--engine",
        "builtin",
        "--source",
        "sqlite_db",
        "--skip-pit-financials",
        "--symbols",
        "600000",
        "--start",
        "2024-01-02",
        "--end",
        "2024-12-31",
        "--report",
        str(report),
        "--gate-path",
        str(tmp_path / "gate.json"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_build_streaming_sqlite_context",
        lambda _args, symbols: (symbols, lambda *_a, **_k: {}, [], {}, {}),
    )
    _patch_engine(monkeypatch, _stub_result())

    assert cli_mod.run_walkforward(args) == 0
    body = report.read_text(encoding="utf-8")
    assert PIT_SKIP_MARKER in body
    for dim in ("quality", "value", "mean_reversion"):
        assert dim in body, f"披露未点名空转维度 {dim}"


# --------------------------------------------------------------------------- #
# 2) 对照：非 streaming 路径的「运行时事实优先」契约不被预初始化破坏
# --------------------------------------------------------------------------- #
def _daily_frame(bars: int = 150) -> pd.DataFrame:
    close = pd.Series(range(1000, 1000 + bars), dtype=float)
    return pd.DataFrame(
        {
            "date": [d.date().isoformat() for d in pd.bdate_range("2024-01-02", periods=bars)],
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000,
            "amount": close * 1_000_000,
        }
    )


def test_non_streaming_report_prefers_runtime_pit_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """非 streaming 路径：``fetch_result.pit_note`` 是**运行时真实事实**，优先于 args 开关。"""
    report = tmp_path / "report.md"
    args = _parse_walkforward_args(
        monkeypatch,
        "walkforward",
        "--engine",
        "builtin",
        "--source",
        "sqlite_db",
        "--symbols",
        "600000",
        "--start",
        "2024-01-02",
        "--end",
        "2024-12-31",
        "--report",
        str(report),
        "--gate-path",
        str(tmp_path / "gate.json"),
    )
    assert getattr(args, "streaming", False) is False

    frames = {"600000": _daily_frame()}
    from aqsp.services import walkforward_data as wd

    monkeypatch.setattr(
        wd,
        "fetch_walkforward_frames",
        lambda *_a, **_k: SimpleNamespace(
            frames=frames, symbols=["600000"], pit_note="运行时事实：本次已跳过 PIT"
        ),
    )
    _patch_engine(monkeypatch, _stub_result())

    assert cli_mod.run_walkforward(args) == 0
    body = report.read_text(encoding="utf-8")
    assert "运行时事实：本次已跳过 PIT" in body, (
        "报告应优先采用 fetch_result.pit_note（运行时真实事实），而不是 args 开关假设"
    )
    assert PIT_SKIP_MARKER not in body, "有真实 pit_note 时不应再打印 args 兜底假设"
