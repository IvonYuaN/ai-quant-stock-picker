"""收盘复盘数据驱动段 + LLM 解读层的单元测试。

覆盖：
- failure_analysis 5 检测器接入（命中/未命中/窗口过滤）；
- build_failure_patterns_section 无命中时显式标注而非空壳；
- build_ai_review_section 降级安全（降级/正常两态，LLM 只进独立小节）；
- format_daily_review 渲染两段、降级时不渲染 AI 段；
- 胜负统计数字断言；
- 通知版含失败模式精简版、不含 AI 段。
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from aqsp.briefing.closing_review import (
    DailyReview,
    build_ai_review_section,
    build_failure_patterns_section,
    format_daily_review,
)
from aqsp.ledger.failure_analysis import analyze_failures_from_file


def _write_loss_ledger(tmp_path, signal_date: str) -> str:
    """写一个近 N 天窗口内、含 4 条 rsi>75 追高 + 4 条深亏跳空亏损的账本。

    深亏 4 条（return_pct < -5）保证 gap_down_after_entry 命中 ≥3 次。
    """
    rows = []
    for i in range(4):
        rows.append(
            {
                "symbol": f"60000{i}",
                "status": "verified",
                "return_pct": -3.0 - i * 0.5,
                "metrics": {"rsi12": 78 + i},
                "strategies": "bowl_rebound",
                "regime_at_signal": "bull",
                "signal_date": signal_date,
            }
        )
    for i in range(4):
        rows.append(
            {
                "symbol": f"60001{i}",
                "status": "verified",
                "return_pct": -8.0 - i,
                "metrics": {"rsi12": 50},
                "strategies": "volume_breakout",
                "regime_at_signal": "bear",
                "signal_date": signal_date,
            }
        )
    ledger = tmp_path / "predictions.jsonl"
    ledger.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    return str(ledger)


def _minimal_review(**overrides) -> DailyReview:
    base = dict(
        date="2026-09-25",
        total_signals=10,
        executed_signals=10,
        win_count=6,
        loss_count=4,
        win_rate=0.6,
        total_return=3.21,
        max_single_win=8.5,
        max_single_loss=-8.0,
        avg_holding_days=3.0,
        strategy_breakdown={"bowl_rebound": {
            "wins": 6, "total": 10, "win_rate": 0.6, "total_return": 3.21,
        }},
        market_environment="震荡偏强",
        main_chain_summary=("主链: 观察复核 600000",),
        key_lessons=("高 RSI 追高是主要亏损来源",),
        improvement_suggestions=("RSI>75 等待回调",),
        ledger_validated_count=10,
        ledger_win_rate=0.6,
        ledger_avg_return=0.5,
        ledger_avg_excess_return=0.2,
        review_scope="近60日平仓 10 笔",
    )
    base.update(overrides)
    return DailyReview(**base)


# --- failure_analysis 接入 -------------------------------------------------


def test_failure_patterns_hit_on_loss_ledger(tmp_path) -> None:
    ledger = _write_loss_ledger(tmp_path, date.today().isoformat())
    patterns = analyze_failures_from_file(ledger, since_date="2000-01-01")
    names = {p.pattern_name for p in patterns}
    assert "high_rsi_entry" in names
    assert "gap_down_after_entry" in names
    assert "weak_market_regime" in names


def test_failure_patterns_window_filter_excludes_old(tmp_path) -> None:
    ledger = _write_loss_ledger(tmp_path, "2000-01-01")
    # 窗口从 2020-01-01 起 → 旧账本被过滤 → 无亏损样本 → 空
    patterns = analyze_failures_from_file(ledger, since_date="2020-01-01")
    assert patterns == []


def test_build_failure_patterns_section_render(tmp_path) -> None:
    ledger = _write_loss_ledger(tmp_path, date.today().isoformat())
    section = build_failure_patterns_section(ledger, window_days=365)
    assert "失败模式分析" in section
    assert "high_rsi_entry" in section
    assert "窗口" in section


def test_build_failure_patterns_section_empty_when_clean(tmp_path) -> None:
    """干净账本（无亏损）→ 显式「无显著失败模式」而非空壳。"""
    ledger = tmp_path / "empty.jsonl"
    ledger.write_text("", encoding="utf-8")
    section = build_failure_patterns_section(ledger, window_days=30)
    assert "无显著失败模式" in section


def test_build_failure_patterns_section_missing_file_means_no_patterns(
    tmp_path,
) -> None:
    """账本文件不存在 = 空账本 = 无亏损样本 → 显式「无显著失败模式」而非异常。"""
    section = build_failure_patterns_section(tmp_path / "nope.jsonl", window_days=30)
    assert "无显著失败模式" in section


def test_build_failure_patterns_section_window_env_override(monkeypatch) -> None:
    from aqsp.briefing import closing_review as cr

    monkeypatch.delenv("AQSP_REVIEW_FAILURE_WINDOW_DAYS", raising=False)
    assert cr._resolve_review_failure_window_days() == 30
    monkeypatch.setenv("AQSP_REVIEW_FAILURE_WINDOW_DAYS", "90")
    assert cr._resolve_review_failure_window_days() == 90
    monkeypatch.setenv("AQSP_REVIEW_FAILURE_WINDOW_DAYS", "bad")
    assert cr._resolve_review_failure_window_days() == 30
    monkeypatch.setenv("AQSP_REVIEW_FAILURE_WINDOW_DAYS", "1")
    assert cr._resolve_review_failure_window_days() == 7  # clamp 下限
    monkeypatch.setenv("AQSP_REVIEW_FAILURE_WINDOW_DAYS", "99999")
    assert cr._resolve_review_failure_window_days() == 180  # clamp 上限


# --- LLM 解读层（降级安全）--------------------------------------------------


def test_ai_section_degraded_returns_empty(tmp_path, monkeypatch) -> None:
    import aqsp.utils.llm_safe as llm

    monkeypatch.setattr(
        llm,
        "llm_call_or_fallback",
        lambda *a, **k: SimpleNamespace(text="", degraded=True, reason="llm_off"),
    )
    text, degraded = build_ai_review_section(_minimal_review(), "## 失败模式分析")
    assert text == ""
    assert degraded is True


def test_ai_section_success_wraps_and_marks_not_degraded(tmp_path, monkeypatch) -> None:
    import aqsp.utils.llm_safe as llm

    monkeypatch.setattr(
        llm,
        "llm_call_or_fallback",
        lambda *a, **k: SimpleNamespace(
            text="近 30 天 4 笔亏损均为 RSI>75 追高，建议回调至 60 再介入。",
            degraded=False,
            reason="",
        ),
    )
    text, degraded = build_ai_review_section(_minimal_review(), "## 失败模式分析")
    assert degraded is False
    assert "AI 复盘解读" in text
    assert "不构成决策依据" in text
    assert "追高" in text


def test_format_daily_review_renders_ai_section_when_present() -> None:
    review = _minimal_review(
        failure_patterns_section="## 失败模式分析（近 30 天窗口）\n\n| a |\n",
        llm_review_text="## AI 复盘解读（仅供参考，不构成决策依据）\n\n要点…",
        llm_degraded=False,
    )
    report = format_daily_review(review)
    assert "失败模式分析" in report
    assert "AI 复盘解读" in report


def test_format_daily_review_hides_ai_section_when_degraded() -> None:
    review = _minimal_review(
        failure_patterns_section="## 失败模式分析（近 30 天窗口）\n\n| a |\n",
        llm_review_text="",
        llm_degraded=True,
    )
    report = format_daily_review(review)
    assert "AI 复盘解读" not in report


def test_win_loss_stats_numbers() -> None:
    review = _minimal_review(
        total_signals=10,
        executed_signals=10,
        win_count=6,
        loss_count=4,
        win_rate=0.6,
        total_return=3.21,
        max_single_win=8.5,
        max_single_loss=-8.0,
    )
    report = format_daily_review(review)
    assert "总信号数: 10" in report
    assert "纸面验证数: 10" in report
    assert "盈利笔数: 6" in report
    assert "亏损笔数: 4" in report
    assert "胜率: 60.0%" in report
    assert "总收益: 3.21%" in report


# --- 通知版 -------------------------------------------------------------


def test_notification_includes_failure_digest_not_ai(tmp_path, monkeypatch) -> None:
    import aqsp.utils.llm_safe as llm

    monkeypatch.setattr(
        llm,
        "llm_call_or_fallback",
        lambda *a, **k: SimpleNamespace(
            text="LLM 生成的复盘解读", degraded=False, reason="",
        ),
    )
    review = _minimal_review(
        failure_patterns_section=(
            "## 失败模式分析（近 30 天窗口）\n\n"
            "| high_rsi_entry | RSI>75 追高（共 4 次） | 4 | -3.38% | 规避 | \n"
            "\n共发现 1 个失败模式\n"
        ),
        llm_review_text="## AI 复盘解读（仅供参考）\n\n LLM 生成的复盘解读",
        llm_degraded=False,
    )
    from aqsp.notify_templates import build_closing_review_notification

    md = build_closing_review_notification(review=review, mode="summary")
    # 失败模式精简版在（带窗口口径的标题 + 「共发现」摘要）
    assert "失败模式分析" in md
    assert "共发现" in md or "规避" in md or "窗口" in md
    # AI 段按设计只进完整报告，不进通知
    assert "AI 复盘解读" not in md


def test_notification_empty_when_no_failure_section(tmp_path) -> None:
    review = _minimal_review(failure_patterns_section="")
    from aqsp.notify_templates import build_closing_review_notification

    md = build_closing_review_notification(review=review, mode="summary")
    assert "失败模式分析" not in md
