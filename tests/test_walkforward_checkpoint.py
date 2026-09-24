"""walk-forward 断点续跑检查点的契约测试。

只覆盖可独立运行、不依赖行情数据的部分：
- ``_append_checkpoint`` / ``_load_checkpoint`` 的序列化往返；
- 半行截断（进程在写某行时被杀）时，已完成行不丢、截断行被安全跳过；
- 文件不存在时返回空（安全降级）。

``run_streaming`` 的端到端续跑（跳过已完成窗口、聚合指标正确）需要 runner 冒烟，
见部署说明：默认不启用，需在 runner 上用一次被打断的真实 gate 验证。
"""

from __future__ import annotations

import os
from dataclasses import asdict

from aqsp.backtest.walk_forward import (
    BacktestResult,
    TradeResult,
    _append_checkpoint,
    _load_checkpoint,
)


def _make_period(period: str) -> BacktestResult:
    return BacktestResult(
        period=period,
        total_return=0.12,
        annual_return=0.3,
        max_drawdown=-0.08,
        sharpe_ratio=1.4,
        win_rate=0.6,
        profit_factor=1.8,
        trades=10,
        not_executable=1,
        skipped=False,
        skipped_periods=0,
        active_periods=10,
        sharpe_ratio_active=1.4,
        win_rate_active=0.6,
    )


def _make_trade(symbol: str) -> TradeResult:
    return TradeResult(
        symbol=symbol,
        signal_date="2020-01-01",
        entry_date="2020-01-02",
        exit_date="2020-01-09",
        entry_price=10.0,
        exit_price=11.2,
        return_pct=0.12,
        exit_reason="horizon",
        market_regime="bull",
        executable=True,
    )


def test_append_then_load_roundtrip(tmp_path):
    ckpt = str(tmp_path / "resume.jsonl")
    p1 = _make_period("2020-01-01 to 2020-02-01")
    t1 = _make_trade("600000")
    _append_checkpoint(ckpt, "2020-01-01..2020-02-01", p1, [t1])

    periods, trades, windows = _load_checkpoint(ckpt)
    assert len(periods) == 1
    assert periods[0] == p1
    assert len(trades) == 1
    assert trades[0] == t1
    assert windows == {"2020-01-01..2020-02-01"}


def test_multiple_periods_preserved(tmp_path):
    ckpt = str(tmp_path / "resume.jsonl")
    for i in range(3):
        p = _make_period(f"w{i}")
        _append_checkpoint(ckpt, f"win{i}", p, [_make_trade(f"60000{i}")])

    periods, trades, windows = _load_checkpoint(ckpt)
    assert len(periods) == 3
    assert len(trades) == 3
    assert windows == {f"win{i}" for i in range(3)}
    # asdict 往返无信息丢失
    assert asdict(periods[0]) == asdict(_make_period("w0"))


def test_truncated_last_line_is_skipped(tmp_path):
    """进程在写第 3 行中途被杀 → 第 1、2 行完好，第 3 行（半截）被跳过，不丢已完成结果。"""
    ckpt = str(tmp_path / "resume.jsonl")
    _append_checkpoint(ckpt, "win0", _make_period("w0"), [_make_trade("600000")])
    _append_checkpoint(ckpt, "win1", _make_period("w1"), [_make_trade("600001")])

    # 追加一个半截（非法 JSON）的最后一行，模拟被杀
    with open(ckpt, "a", encoding="utf-8") as fh:
        fh.write('{"window": "win2", "result": {"period": "w2", \n')  # 故意截断

    periods, trades, windows = _load_checkpoint(ckpt)
    assert len(periods) == 2
    assert windows == {"win0", "win1"}


def test_missing_file_returns_empty(tmp_path):
    periods, trades, windows = _load_checkpoint(str(tmp_path / "nope.jsonl"))
    assert periods == []
    assert trades == []
    assert windows == set()


def test_checkpoint_file_is_640(tmp_path):
    ckpt = str(tmp_path / "resume.jsonl")
    _append_checkpoint(ckpt, "win0", _make_period("w0"), [])
    mode = os.stat(ckpt).st_mode & 0o777
    assert mode == 0o640, f"expected 0640, got {oct(mode)}"
