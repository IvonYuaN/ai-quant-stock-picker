"""绩效只读桥接的契约测试。

重点不是"能算出数字"，而是**该给数字的时候才给**：
宪法 §5.4 规定冷启动期（独立信号日 < 30）不展示胜率 ——
这条一旦失守，用户会拿一个没有统计意义的数字去做决策。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("VR_DATA_DIR", "/tmp/aqsp-vibe-perf-test-data")

import performance_bridge as pb  # noqa: E402


def _row(signal_date: str, ret: float, strategies=("rps_momentum",), status="validated"):
    return {
        "signal_date": signal_date,
        "status": status,
        "return_pct": ret,
        "strategies": list(strategies),
        "horizon_days": 3,
    }


def _write_ledger(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "predictions.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def _payload(tmp_path: Path, rows: list[dict], monkeypatch) -> dict:
    path = _write_ledger(tmp_path, rows)
    monkeypatch.setattr(pb, "_ledger_path", lambda: path)
    monkeypatch.setattr(pb, "_weight_history_path", lambda: tmp_path / "weight_history.jsonl")
    return pb.performance_payload()


def test_missing_ledger_is_honest_not_error(tmp_path, monkeypatch):
    """新装环境没有台账是正常状态，不该报错，也不该编造数字。"""
    monkeypatch.setattr(pb, "_ledger_path", lambda: tmp_path / "nope.jsonl")
    payload = pb.performance_payload()
    assert payload["available"] is False
    assert "未找到台账文件" in payload["reason"]
    assert payload["strategies"] == []


def test_cold_start_suppresses_hit_rate(tmp_path, monkeypatch):
    """§5.4：独立信号日 < 30 时，displayable 必须为 False。"""
    rows = [_row(f"2026-01-{d:02d}", 1.5 if d % 2 else -0.5) for d in range(1, 10)]  # 9 天
    payload = _payload(tmp_path, rows, monkeypatch)

    assert payload["available"] is True
    assert payload["cold_start"]["is_cold_start"] is True
    assert payload["cold_start"]["independent_signal_days"] == 9
    assert payload["overall"]["displayable"] is False
    for strategy in payload["strategies"]:
        assert strategy["displayable"] is False
        # 冷启动期权重固定 1.0（§5.4）
        assert strategy["weight_base"] == 1.0


def test_warm_up_enables_hit_rate(tmp_path, monkeypatch):
    """样本够时（≥30 独立信号日）才允许展示胜率。"""
    rows = [_row(f"2026-{m:02d}-{d:02d}", 1.0) for m in (1, 2) for d in range(1, 17)]  # 32 天
    payload = _payload(tmp_path, rows, monkeypatch)

    assert payload["cold_start"]["independent_signal_days"] == 32
    assert payload["overall"]["displayable"] is True
    assert payload["overall"]["hit_rate"] == 1.0


def test_overall_aggregates_per_signal_day(tmp_path, monkeypatch):
    """§5.2：同一信号日多只 pick 合成 1 个观察，不能按每笔算。"""
    rows = [
        # 同一天：+10 与 -8 → 观察值 +1（正）→ 命中 1 天
        _row("2026-01-05", 10.0),
        _row("2026-01-05", -8.0),
        # 另一天：单只 -5 → 观察值 -5 → 不命中
        _row("2026-01-06", -5.0),
    ]
    payload = _payload(tmp_path, rows, monkeypatch)

    # 2 个观察日，1 天为正 → 50%，而不是按 3 笔算成 33%
    assert payload["overall"]["observations"] == 2
    assert payload["overall"]["win_count"] == 1
    assert payload["overall"]["hit_rate"] == pytest.approx(0.5)


def test_not_validated_rows_excluded(tmp_path, monkeypatch):
    """pending / not_executable 等不得进入统计。"""
    rows = [
        _row("2026-01-05", 10.0, status="validated"),
        _row("2026-01-06", 10.0, status="pending"),
        _row("2026-01-07", 10.0, status="not_executable"),
    ]
    payload = _payload(tmp_path, rows, monkeypatch)
    assert payload["overall"]["observations"] == 1
    assert payload["status_counts"]["pending"] == 1
    assert payload["status_counts"]["not_executable"] == 1


def test_simulated_rows_excluded(tmp_path, monkeypatch):
    """回测/模拟行不是生产观测，必须剔除。"""
    rows = [
        {**_row("2026-01-05", 10.0), "is_simulated": True},
        _row("2026-01-06", -2.0),
    ]
    payload = _payload(tmp_path, rows, monkeypatch)
    assert payload["overall"]["observations"] == 1
    assert payload["overall"]["hit_rate"] == 0.0


def test_notes_declare_constitution_rules(tmp_path, monkeypatch):
    """口径说明必须写进 payload，避免前端自行解释。"""
    rows = [_row(f"2026-01-{d:02d}", 1.0) for d in range(1, 5)]
    payload = _payload(tmp_path, rows, monkeypatch)
    notes = " ".join(payload["notes"])
    assert "signal_date" in notes
    assert "not_executable" in notes
    assert "冷启动" in notes or "30" in notes
    assert "PnL" in notes


def test_payload_is_read_only(tmp_path, monkeypatch):
    """只读桥接：不得写台账、不得产生权重历史文件。"""
    path = _write_ledger(tmp_path, [_row("2026-01-05", 1.0)])
    weight_history = tmp_path / "weight_history.jsonl"
    monkeypatch.setattr(pb, "_ledger_path", lambda: path)
    monkeypatch.setattr(pb, "_weight_history_path", lambda: weight_history)
    before = path.read_text(encoding="utf-8")
    pb.performance_payload()
    assert path.read_text(encoding="utf-8") == before
    assert not weight_history.exists()


def test_learner_failure_degrades_without_crashing(tmp_path, monkeypatch):
    """学习器内部异常不得让只读接口 500：退回空表，并在 notes 里说明降级。

    契约：`available` 仍为 True（台账读到了，整体命中率仍可信），
    但策略级列表为空且给出原因 —— 前端据此只隐藏策略级卡片，而不是显示"服务错误"。
    """

    class _BoomLearner:
        def __init__(self, *args, **kwargs):
            pass

        def learn_from_ledger(self, *args, **kwargs):
            raise RuntimeError("learner exploded")

    monkeypatch.setattr(pb, "PerformanceLearner", _BoomLearner)
    payload = _payload(tmp_path, [_row("2026-01-05", 1.0)], monkeypatch)

    assert payload["available"] is True
    assert payload["strategies"] == []
    assert any("策略级学习器未完成" in note for note in payload["notes"])
    # 整体命中率不依赖 learner，仍必须给出
    assert payload["overall"]["observations"] == 1


def test_learner_constructor_failure_degrades_without_crashing(tmp_path, monkeypatch):
    """连构造都失败（缺权重历史目录、配置异常等）也不能 500。"""

    class _BoomInit:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("cannot build learner")

    monkeypatch.setattr(pb, "PerformanceLearner", _BoomInit)
    payload = _payload(tmp_path, [_row("2026-01-05", 1.0)], monkeypatch)

    assert payload["available"] is True
    assert payload["strategies"] == []
    assert any("策略级学习器未完成" in note for note in payload["notes"])


# --------------------------------------------------------------------------
# 新鲜度：必须能区分"正在积累"与"已经停止更新"
# --------------------------------------------------------------------------


def _freeze_now(monkeypatch, iso_date: str):
    """把"现在"固定下来，让交易日计算可复现。"""
    from datetime import date as _date
    from datetime import datetime as _datetime

    from aqsp.core.time import SHANGHAI_TZ

    fixed = _datetime.combine(
        _date.fromisoformat(iso_date), _datetime.min.time(), tzinfo=SHANGHAI_TZ
    )
    monkeypatch.setattr(pb, "now_shanghai", lambda: fixed)


def test_freshness_reports_latest_signal_date(tmp_path, monkeypatch):
    rows = [_row("2026-01-05", 1.0), _row("2026-01-09", 1.0), _row("2026-01-07", 1.0)]
    payload = _payload(tmp_path, rows, monkeypatch)
    assert payload["freshness"]["latest_signal_date"] == "2026-01-09"
    assert payload["freshness"]["ledger_updated_at"] != ""


def test_recent_signal_is_not_stale(tmp_path, monkeypatch):
    """周四的信号，周五看：只过了 1 个交易日，不该报停滞。"""
    _freeze_now(monkeypatch, "2026-09-18")  # 周五
    payload = _payload(tmp_path, [_row("2026-09-17", 1.0)], monkeypatch)
    assert payload["freshness"]["trading_days_since_latest"] == 1
    assert payload["freshness"]["stale"] is False


def test_long_gap_is_stale(tmp_path, monkeypatch):
    """跨多周未更新 → 停滞，避免用户以为样本还在攒。"""
    _freeze_now(monkeypatch, "2026-09-18")
    payload = _payload(tmp_path, [_row("2026-08-28", 1.0)], monkeypatch)
    days = payload["freshness"]["trading_days_since_latest"]
    assert days is not None and days > payload["freshness"]["stale_after_trading_days"]
    assert payload["freshness"]["stale"] is True


def test_long_holiday_is_not_stale(tmp_path, monkeypatch):
    """长假不该被误判为停滞 —— 这正是用交易日而非自然日的原因。"""
    _freeze_now(monkeypatch, "2026-10-09")  # 节后
    payload = _payload(tmp_path, [_row("2026-09-30", 1.0)], monkeypatch)  # 节前
    days = payload["freshness"]["trading_days_since_latest"]
    # 自然日过了 9 天，但交易日远少于这个数
    assert days is not None and days < 9


def test_missing_signal_date_is_safe(tmp_path, monkeypatch):
    """没有 signal_date 时不能谎称"未更新"。"""
    path = _write_ledger(tmp_path, [{"status": "validated", "return_pct": 1.0}])
    monkeypatch.setattr(pb, "_ledger_path", lambda: path)
    monkeypatch.setattr(pb, "_weight_history_path", lambda: tmp_path / "w.jsonl")
    payload = pb.performance_payload()
    assert payload["freshness"]["latest_signal_date"] == ""
    assert payload["freshness"]["trading_days_since_latest"] is None
    assert payload["freshness"]["stale"] is False


def test_unavailable_payload_has_same_cold_start_fields(tmp_path, monkeypatch):
    """不可用分支与正常分支的字段名必须一致。

    曾经不可用分支写的是 max_independent_signal_days，而正常分支是
    independent_signal_days —— 前端按后者读会静默拿到 undefined。
    """
    monkeypatch.setattr(pb, "_ledger_path", lambda: tmp_path / "nope.jsonl")
    payload = pb.performance_payload()
    assert "independent_signal_days" in payload["cold_start"]
    assert "max_independent_signal_days" not in payload["cold_start"]
    assert payload["freshness"]["stale"] is False
    assert payload["freshness"]["trading_days_since_latest"] is None


def test_notes_declare_staleness_rule(tmp_path, monkeypatch):
    """口径说明要写清停滞是怎么判的。"""
    payload = _payload(tmp_path, [_row("2026-01-05", 1.0)], monkeypatch)
    assert "交易日" in " ".join(payload["notes"])


# --------------------------------------------------------------------------
# 票级复盘明细（recent_picks）：回答"上次具体选了哪只票、事后如何"
# --------------------------------------------------------------------------


def _pick_row(signal_date, symbol="600000", name="浦发银行", ret=3.2, excess=1.1,
              win=True, exit_reason="horizon_close", strategies=("rps_momentum", "ma_pullback")):
    return {
        "signal_date": signal_date,
        "status": "validated",
        "symbol": symbol,
        "name": name,
        "return_pct": ret,
        "excess_return_pct": excess,
        "win": win,
        "exit_reason": exit_reason,
        "strategies": list(strategies),
        "horizon_days": 3,
        "entry_date": signal_date,
        "exit_date": "2026-01-09",
    }


def test_recent_picks_returns_validated_rows_new_first(tmp_path, monkeypatch):
    """按 signal_date 新→旧；字段与台账原值一一对应，不做二次计算。"""
    rows = [
        _pick_row("2026-01-05", symbol="600000", name="浦发银行", ret=3.2, excess=1.1, win=True),
        _pick_row("2026-01-06", symbol="000001", name="平安银行", ret=-1.4, excess=-0.8,
                  win=False, exit_reason="stop_loss"),
        _pick_row("2026-01-07", symbol="600519", name="贵州茅台", ret=0.5, excess=0.0,
                  win=True, exit_reason="take_profit"),
    ]
    payload = _payload(tmp_path, rows, monkeypatch)
    picks = payload["recent_picks"]
    assert [p["signal_date"] for p in picks] == ["2026-01-07", "2026-01-06", "2026-01-05"]
    newest = picks[0]
    assert newest["symbol"] == "600519"
    assert newest["name"] == "贵州茅台"
    assert newest["return_pct"] == 0.5
    assert newest["excess_return_pct"] == 0.0
    assert newest["win"] is True
    assert newest["exit_reason"] == "take_profit"
    assert newest["strategies"] == ["rps_momentum", "ma_pullback"]


def test_recent_picks_excludes_non_validated(tmp_path, monkeypatch):
    """pending / not_executable / 模拟行不得进复盘表。"""
    rows = [
        _pick_row("2026-01-05"),
        {**_pick_row("2026-01-06"), "status": "pending"},
        {**_pick_row("2026-01-07"), "status": "not_executable"},
        {**_pick_row("2026-01-08"), "is_simulated": True},
    ]
    payload = _payload(tmp_path, rows, monkeypatch)
    assert [p["signal_date"] for p in payload["recent_picks"]] == ["2026-01-05"]


def test_recent_picks_capped_at_limit(tmp_path, monkeypatch):
    """展示侧截断：超过上限只回最近 N 笔，不影响聚合口径。"""
    rows = [_pick_row(f"2026-01-{d:02d}", symbol=f"{600000 + d}") for d in range(1, 31)]  # 30 天
    payload = _payload(tmp_path, rows, monkeypatch)
    assert len(payload["recent_picks"]) == pb.RECENT_PICKS_LIMIT
    # 截断必须是"最新的 N 笔"：首条应是最大 signal_date
    assert payload["recent_picks"][0]["signal_date"] == "2026-01-30"


def test_recent_picks_missing_excess_is_none_not_zero(tmp_path, monkeypatch):
    """excess_return_pct 缺失时如实给 None，不回填 0 冒充"没跑赢基准"。"""
    rows = [
        {
            "signal_date": "2026-01-05",
            "status": "validated",
            "symbol": "600000",
            "name": "浦发银行",
            "return_pct": 2.0,
            "win": True,
            "exit_reason": "horizon_close",
            "strategies": ["rps_momentum"],
            "horizon_days": 3,
        }
    ]
    payload = _payload(tmp_path, rows, monkeypatch)
    assert payload["recent_picks"][0]["excess_return_pct"] is None


def test_recent_picks_unavailable_branch_has_same_key(tmp_path, monkeypatch):
    """不可用分支与正常分支字段名一致（recent_picks 键必须存在且为空）。"""
    monkeypatch.setattr(pb, "_ledger_path", lambda: tmp_path / "nope.jsonl")
    payload = pb.performance_payload()
    assert "recent_picks" in payload
    assert payload["recent_picks"] == []


def test_recent_picks_empty_when_no_validated(tmp_path, monkeypatch):
    """只有 pending 行时复盘表为空，但 available 仍为真（台账读到了）。"""
    rows = [
        {**_pick_row("2026-01-05"), "status": "pending"},
        {**_pick_row("2026-01-06"), "status": "pending"},
    ]
    payload = _payload(tmp_path, rows, monkeypatch)
    assert payload["available"] is True
    assert payload["recent_picks"] == []


# --------------------------------------------------------------------------