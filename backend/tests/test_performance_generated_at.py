"""绩效载荷的生成时刻必须走上海时区（AGENTS.md §3.4：禁裸 `datetime.now()`）。

`performance_bridge.py` 曾有两处 `datetime.now().strftime(...)`，而
`now_shanghai` 明明已经从 `aqsp.core.time` 导入了 —— 属于疏漏不是设计。

后果取决于机器时区：prod 是 CST，看不出问题；但 **runner 是 UTC**，同一份代码
跑在那里会把 UTC 时刻当成北京时间显示，**差 8 小时**。这类 bug 不会报错，
只会让「生成时间」这句陈述变假。

两处现在共用 `_generated_at()`，下面用「注入固定时钟」的方式验证它确实用了
上海时区来源，而不是碰巧在本机时区正确。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import performance_bridge

_UTC = timezone.utc
_CST = timezone(timedelta(hours=8))


def test_generated_at_uses_shanghai_clock(monkeypatch) -> None:
    """注入一个 UTC 时刻，输出必须是它的北京时间表示。"""
    monkeypatch.setattr(
        performance_bridge, "now_shanghai", lambda: datetime(2026, 10, 19, 3, 30, tzinfo=_UTC).astimezone(_CST)
    )
    assert performance_bridge._generated_at() == "2026-10-19 11:30"


def test_generated_at_reflects_injected_clock(monkeypatch) -> None:
    """换一个时刻输出要跟着变 —— 排除「硬编码/取本机时间」的假通过。"""
    monkeypatch.setattr(
        performance_bridge, "now_shanghai", lambda: datetime(2026, 1, 2, 3, 4, tzinfo=_CST)
    )
    assert performance_bridge._generated_at() == "2026-01-02 03:04"


def test_unavailable_payload_carries_shanghai_time(monkeypatch) -> None:
    """`_unavailable()` 走的是同一个助手（台账缺失是常态路径，最常被看到）。"""
    monkeypatch.setattr(
        performance_bridge, "now_shanghai", lambda: datetime(2026, 10, 19, 3, 30, tzinfo=_UTC).astimezone(_CST)
    )
    payload = performance_bridge._unavailable("台账为空")
    assert payload["generated_at"] == "2026-10-19 11:30"
    assert payload["available"] is False


@pytest.mark.parametrize("hour", [0, 3, 15, 23])
def test_generated_at_never_shifts_by_host_timezone(monkeypatch, hour) -> None:
    """同一条 UTC 时刻恒为 +8 小时 —— 与本机 TZ 无关（23 点会跨日）。"""
    moment = datetime(2026, 10, 19, hour, 0, tzinfo=_UTC)
    monkeypatch.setattr(performance_bridge, "now_shanghai", lambda: moment.astimezone(_CST))
    assert performance_bridge._generated_at() == (moment + timedelta(hours=8)).strftime(
        "%Y-%m-%d %H:%M"
    )


# ---------------- 聚合超额：诚实边界 ----------------

def _write_ledger(path, rows):
    import json
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


def test_excess_summary_aggregates_available_rows(monkeypatch, tmp_path):
    """有若干行带 excess_return_pct 时，均值/覆盖率必须基于实际可用行。"""
    import performance_bridge as pb

    path = tmp_path / "pred.jsonl"
    _write_ledger(path, [
        {"id": "1", "signal_date": "2026-09-01", "status": "validated",
         "excess_return_pct": 3.0, "return_pct": 5.0},
        {"id": "2", "signal_date": "2026-09-02", "status": "validated",
         "excess_return_pct": -1.0, "return_pct": 1.0},
        {"id": "3", "signal_date": "2026-09-03", "status": "validated",
         "return_pct": 2.0},  # 无超额 → 不计入均值，但要计入 n_settled
    ])
    monkeypatch.setenv("AQSP_LEDGER", str(path))
    payload = pb.performance_payload()
    ex = payload["excess"]
    assert ex["available"] is True
    assert ex["n_settled"] == 3
    assert ex["n_with_excess"] == 2
    assert ex["coverage"] == round(2 / 3, 4)
    assert ex["avg_excess_pct"] == round(1.0, 4)


def test_excess_summary_refuses_zero_when_all_missing(monkeypatch, tmp_path):
    """全部缺失时必须 available=False —— 返回 0 会被读成"没跑赢也没跑输"。"""
    import performance_bridge as pb

    path = tmp_path / "pred.jsonl"
    _write_ledger(path, [
        {"id": "1", "signal_date": "2026-09-01", "status": "validated", "return_pct": 5.0},
    ])
    monkeypatch.setenv("AQSP_LEDGER", str(path))
    ex = pb.performance_payload()["excess"]
    assert ex["available"] is False
    assert "avg_excess_pct" not in ex
