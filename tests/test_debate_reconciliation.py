"""debate ↔ 实盘战绩对账（只读）单测。

覆盖：分桶统计正确性、干预 vs 未干预判读三态、空样本降级、
窗口过滤、since_date 边界、渲染格式。
"""

from __future__ import annotations


import pandas as pd
import pytest

from aqsp.ledger.debate_reconciliation import (
    format_debate_reconciliation,
    reconcile_debate,
    reconcile_debate_from_file,
)


def _ledger_rows(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


@pytest.fixture
def ledger_file(tmp_path):
    path = tmp_path / "ledger.jsonl"
    rows = [
        # 干预桶：blocked（避险，赚）
        {"symbol": "A", "status": "closed", "return_pct": 3.0,
         "debate_action_influence": "blocked", "signal_date": "2026-09-20"},
        {"symbol": "B", "status": "closed", "return_pct": 1.0,
         "debate_action_influence": "blocked", "signal_date": "2026-09-21"},
        # 干预桶：downgraded（赚一点）
        {"symbol": "C", "status": "closed", "return_pct": 2.0,
         "debate_action_influence": "downgraded", "signal_date": "2026-09-22"},
        # 未干预桶：none（亏）
        {"symbol": "D", "status": "closed", "return_pct": -5.0,
         "debate_action_influence": "none", "signal_date": "2026-09-20"},
        {"symbol": "E", "status": "closed", "return_pct": -3.0,
         "debate_action_influence": "rules_only", "signal_date": "2026-09-21"},
        {"symbol": "F", "status": "closed", "return_pct": -2.0,
         "debate_action_influence": "no_debate", "signal_date": "2026-09-22"},
        # 未实现 / 不可执行 → 不计
        {"symbol": "G", "status": "pending", "return_pct": None,
         "debate_action_influence": "none", "signal_date": "2026-09-23"},
        {"symbol": "H", "status": "not_executable", "return_pct": -1.0,
         "debate_action_influence": "none", "signal_date": "2026-09-23"},
    ]
    path.write_text("\n".join(__import__("json").dumps(r, ensure_ascii=False)
                              for r in rows), encoding="utf-8")
    return str(path)


class TestReconcileDebate:
    def test_bucket_statistics(self, ledger_file):
        rec = reconcile_debate_from_file(ledger_file)
        assert rec.has_sample is True
        by_bucket = {row.bucket: row for row in rec.rows}

        # none 桶：1 条，亏 5，胜率 0
        assert by_bucket["none"].count == 1
        assert by_bucket["none"].win_count == 0
        assert by_bucket["none"].avg_return == -5.0
        assert by_bucket["none"].total_return == -5.0

        # rules_only 桶：1 条，亏 3
        assert by_bucket["rules_only"].count == 1
        assert by_bucket["rules_only"].avg_return == -3.0

        # no_debate 桶：1 条，亏 2
        assert by_bucket["no_debate"].avg_return == -2.0

        # blocked 桶：2 条，全赢（3、1）
        assert by_bucket["blocked"].count == 2
        assert by_bucket["blocked"].win_count == 2
        assert by_bucket["blocked"].win_rate == 1.0
        assert by_bucket["blocked"].avg_return == 2.0

        # downgraded 桶：1 条，赚 2
        assert by_bucket["downgraded"].count == 1
        assert by_bucket["downgraded"].avg_return == 2.0

    def test_pending_and_not_executable_excluded(self, ledger_file):
        rec = reconcile_debate_from_file(ledger_file)
        # G/H 两条不计入任何桶（总桶数 = 6）
        total = sum(r.count for r in rec.rows)
        assert total == 6

    def test_intervention_vs_non_intervention_verdict_positive(self, ledger_file):
        rec = reconcile_debate_from_file(ledger_file)
        # 干预桶 avg = (3+1+2)/3 = 2.0；未干预 avg = (-5-3-2)/3 = -3.33
        assert rec.intervention_avg is not None
        assert rec.non_intervention_avg is not None
        assert rec.intervention_avg > rec.non_intervention_avg
        section = format_debate_reconciliation(rec, window_days=30)
        assert "避险" in section or "价值为正" in section

    def test_empty_sample_degraded(self):
        rec = reconcile_debate(_ledger_rows([]))
        assert rec.has_sample is False
        section = format_debate_reconciliation(rec, window_days=30)
        assert "无可度量" in section

    def test_no_return_pct_rows(self):
        df = _ledger_rows([
            {"symbol": "X", "status": "closed", "return_pct": None,
             "debate_action_influence": "none"},
        ])
        rec = reconcile_debate(df)
        assert rec.has_sample is False

    def test_missing_debate_field_lands_in_unknown_bucket(self):
        df = _ledger_rows([
            {"symbol": "Y", "status": "closed", "return_pct": 1.0},
        ])
        rec = reconcile_debate(df)
        assert rec.has_sample is True
        # 无 debate_action_influence 字段的行归入 '' 桶
        keys = [r.bucket for r in rec.rows]
        assert "" in keys

    def test_since_date_filter(self):
        df = _ledger_rows([
            {"symbol": "A", "status": "closed", "return_pct": 3.0,
             "debate_action_influence": "blocked", "signal_date": "2026-09-20"},
            {"symbol": "B", "status": "closed", "return_pct": -1.0,
             "debate_action_influence": "none", "signal_date": "2026-09-01"},
        ])
        rec = reconcile_debate(df, since_date="2026-09-15")
        # 只保留 09-20 那条
        total = sum(r.count for r in rec.rows)
        assert total == 1

    def test_intervention_avg_none_when_no_intervention_rows(self):
        df = _ledger_rows([
            {"symbol": "Z", "status": "closed", "return_pct": 1.0,
             "debate_action_influence": "none"},
        ])
        rec = reconcile_debate(df)
        assert rec.has_sample is True
        assert rec.intervention_avg is None  # 无干预桶
        section = format_debate_reconciliation(rec, window_days=7)
        assert "不足以做" in section


class TestFormatReconciliation:
    def test_header_includes_window(self, ledger_file):
        rec = reconcile_debate_from_file(ledger_file)
        section = format_debate_reconciliation(rec, window_days=30)
        assert "近 30 天窗口" in section
        assert "| debate 行为 |" in section

    def test_unknown_bucket_label(self):
        df = _ledger_rows([
            {"symbol": "U", "status": "closed", "return_pct": 1.0,
             "debate_action_influence": "some_future_value"},
        ])
        rec = reconcile_debate(df)
        section = format_debate_reconciliation(rec)
        # 未知值归入（未记录）
        assert "（未记录）" in section

    def test_no_window_header(self, ledger_file):
        rec = reconcile_debate_from_file(ledger_file)
        section = format_debate_reconciliation(rec)
        assert "debate ↔ 实盘战绩对账" in section
