from __future__ import annotations

from datetime import date

from aqsp.walkforward_gate import (
    MIN_PRODUCTION_GATE_COVERAGE_RATIO,
    MIN_PRODUCTION_GATE_SYMBOLS,
    build_walkforward_gate_evidence,
    build_walkforward_gate_payload,
    display_thresholds_version,
    validate_walkforward_gate_payload,
    validate_walkforward_market_coverage,
)


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload = build_walkforward_gate_payload(
        dsr=1.9,
        pbo=0.24,
        run_date="2026-06-10",
        start="2023-01-01",
        end="2024-12-31",
        n_periods=12,
        n_variants=8,
    )
    payload.update(overrides)
    return payload


def test_walkforward_gate_validates_pass_when_payload_is_strict_and_clean() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(),
        today=date(2026, 6, 14),
        heldout_cutoff=date(2024, 12, 31),
    )

    assert result.ok is True
    assert result.blockers == ()


def test_walkforward_gate_evidence_preserves_real_dsr_and_pbo_failures() -> None:
    evidence = build_walkforward_gate_evidence(
        build_walkforward_gate_payload(
            dsr=-1.2794,
            pbo=1.0,
            run_date="2026-06-30",
            start="2023-06-30",
            end="2026-06-29",
            n_periods=19,
            n_variants=8,
        ),
        today=date(2026, 7, 13),
    )

    assert evidence.ok is False
    assert evidence.status == "fail"
    assert evidence.dsr == -1.2794
    assert evidence.pbo == 1.0
    assert evidence.n_periods == 19
    assert any(item.startswith("DSR=") for item in evidence.reasons)
    assert any(item.startswith("PBO=") for item in evidence.reasons)


def test_walkforward_gate_rejects_threshold_version_mismatch_for_proposal() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(thresholds_version="old"),
        today=date(2026, 6, 14),
        expected_thresholds_version="current",
    )

    assert result.ok is False
    assert result.thresholds_version == "old"
    assert any("thresholds_version mismatch" in item for item in result.blockers)


def test_walkforward_gate_requires_assumption_audit_for_proposal_evidence() -> None:
    evidence = build_walkforward_gate_evidence(
        _valid_payload(thresholds_version="current"),
        today=date(2026, 6, 14),
        expected_thresholds_version="current",
        require_assumption_audit=True,
    )

    assert evidence.ok is False
    assert "assumption_audit missing/invalid" in evidence.reasons


def test_walkforward_gate_accepts_matching_version_and_clean_assumptions() -> None:
    evidence = build_walkforward_gate_evidence(
        _valid_payload(
            thresholds_version="current",
            backtest_assumptions={
                "uses_raw_prices": True,
                "uses_point_in_time_data": True,
                "train_test_separated": True,
                "has_purge_window": True,
                "includes_transaction_costs": True,
                "includes_slippage": True,
                "excludes_not_executable": True,
                "cost_model": "fee+slippage from thresholds",
            },
        ),
        today=date(2026, 6, 14),
        expected_thresholds_version="current",
        require_assumption_audit=True,
    )

    assert evidence.ok is True
    assert evidence.thresholds_version == "current"


def test_walkforward_gate_rejects_string_booleans_and_string_metrics() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(
            deflated_sharpe="1.9",
            pbo="0.24",
            pbo_valid="true",
            dsr_pass="true",
            pbo_pass="true",
            both_pass="true",
        ),
        today=date(2026, 6, 14),
    )

    assert result.ok is False
    assert "deflated_sharpe missing/invalid" in result.blockers
    assert "pbo missing/invalid" in result.blockers
    assert "both_pass flag missing/invalid/false" in result.blockers


def test_walkforward_gate_rejects_bool_nan_and_non_int_periods() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(deflated_sharpe=True, pbo=float("nan"), n_periods=True),
        today=date(2026, 6, 14),
    )

    assert result.ok is False
    assert "deflated_sharpe missing/invalid" in result.blockers
    assert "pbo missing/invalid" in result.blockers
    assert "n_periods missing/invalid" in result.blockers


def test_walkforward_gate_recomputes_metric_thresholds_instead_of_trusting_flags() -> (
    None
):
    result = validate_walkforward_gate_payload(
        _valid_payload(deflated_sharpe=0.8, pbo=0.0),
        today=date(2026, 6, 14),
    )

    assert result.ok is False
    assert "DSR=0.8000 <= 1.0" in result.blockers
    assert "PBO=0.00% outside (0%, 50%)" in result.blockers


def test_walkforward_gate_rejects_stale_and_heldout_payloads() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(run_date="2026-01-01", data_end="2026-04-30"),
        today=date(2026, 6, 14),
        heldout_cutoff=date(2024, 12, 31),
    )

    assert result.ok is False
    assert "gate stale: 164 days > 35" in result.blockers
    assert "data_end=2026-04-30 > heldout_cutoff=2024-12-31" in result.blockers


def test_walkforward_market_coverage_passes_full_market_gate() -> None:
    result = validate_walkforward_market_coverage(
        _valid_payload(
            effective_symbols=5000,
            production_gate_coverage={"stock_symbols": 5533},
        )
    )

    assert result.ok is True
    assert result.effective_symbols == 5000
    assert result.blockers == ()


def test_walkforward_market_coverage_rejects_missing_and_bool_counts() -> None:
    missing = validate_walkforward_market_coverage(_valid_payload())
    boolean = validate_walkforward_market_coverage(
        _valid_payload(effective_symbols=True)
    )

    assert missing.ok is False
    assert missing.effective_symbols is None
    assert "effective_symbols missing/invalid" in missing.blockers
    assert boolean.ok is False
    assert boolean.effective_symbols is None
    assert "effective_symbols missing/invalid" in boolean.blockers


def test_walkforward_market_coverage_rejects_smoke_sample_counts() -> None:
    result = validate_walkforward_market_coverage(_valid_payload(effective_symbols=300))

    assert result.ok is False
    assert result.effective_symbols == 300
    assert (
        f"effective_symbols=300 < required_symbols={MIN_PRODUCTION_GATE_SYMBOLS}"
        in result.blockers
    )


def test_walkforward_market_coverage_rejects_partial_full_market_ratio() -> None:
    result = validate_walkforward_market_coverage(
        _valid_payload(
            effective_symbols=3200,
            production_gate_coverage={"stock_symbols": 5533},
        )
    )

    assert result.ok is False
    assert result.coverage_ratio is not None
    assert result.coverage_ratio < MIN_PRODUCTION_GATE_COVERAGE_RATIO
    assert "required_symbols=4980" in result.blockers[0]


def test_walkforward_market_coverage_prefers_selected_symbols_denominator() -> None:
    result = validate_walkforward_market_coverage(
        _valid_payload(
            effective_symbols=5157,
            production_gate_coverage={
                "stock_symbols": 5797,
                "selected_symbols": 5193,
            },
        )
    )

    assert result.ok is True
    assert result.stock_symbols == 5193
    assert result.required_symbols == 4674


def test_walkforward_gate_allows_recent_window_beyond_heldout_cutoff() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(
            data_end="2026-06-20",
            window_mode="rolling_recent",
            coverage_mode="auto_recent_window",
        ),
        today=date(2026, 6, 21),
        heldout_cutoff=date(2024, 12, 31),
    )

    assert result.ok is True


def test_walkforward_gate_blocks_when_assumption_audit_fails() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(
            backtest_assumptions={
                "uses_raw_prices": False,
                "uses_point_in_time_data": True,
                "train_test_separated": True,
                "has_purge_window": True,
                "includes_transaction_costs": True,
                "includes_slippage": False,
                "excludes_not_executable": True,
                "cost_model": "",
            }
        ),
        today=date(2026, 6, 14),
    )

    assert result.ok is False
    assert result.assumption_audit_ok is False
    assert any("uses_raw_prices" in item for item in result.assumption_audit_blockers)
    assert any(
        "assumption_audit: includes_slippage" in item for item in result.blockers
    )


def test_walkforward_gate_accepts_clean_assumption_audit() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(
            backtest_assumptions={
                "uses_raw_prices": True,
                "uses_point_in_time_data": True,
                "train_test_separated": True,
                "has_purge_window": True,
                "includes_transaction_costs": True,
                "includes_slippage": True,
                "excludes_not_executable": True,
                "cost_model": "fee+slippage from thresholds",
            }
        ),
        today=date(2026, 6, 14),
    )

    assert result.ok is True
    assert result.assumption_audit_ok is True
    assert result.assumption_audit_blockers == ()


def test_display_thresholds_version_returns_unknown_when_missing() -> None:
    # 健康报告 #R8：gate sidecar 缺失 thresholds_version 时，展示/审计输出
    # 必须是可读的 "unknown"，而非空白（空白会掩盖「gate 未记录版本」这一事实）。
    assert display_thresholds_version(None) == "unknown"
    assert display_thresholds_version("") == "unknown"
    assert display_thresholds_version("   ") == "unknown"


def test_display_thresholds_version_passthrough_when_present() -> None:
    # 仅做空白清理，真实版本号原样返回；不应把合法版本也兜底成 unknown。
    assert display_thresholds_version("thresholds@2026.09.05") == "thresholds@2026.09.05"
    assert display_thresholds_version("  v2  ") == "v2"


def test_walkforward_gate_detail_renders_unknown_when_version_missing() -> None:
    # 生产 data/walkforward_gate.json 当前即无 thresholds_version 字段，
    # 校验必须通过但 detail 必须显示 unknown（而非空白）。
    result = validate_walkforward_gate_payload(
        _valid_payload(),  # 不传 thresholds_version → payload 无该字段
        today=date(2026, 6, 14),
    )
    assert result.ok is True
    assert result.thresholds_version is None
    assert "thresholds_version=unknown" in result.detail


def test_walkforward_gate_detail_renders_version_when_present() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(thresholds_version="thresholds@2026.09.05"),
        today=date(2026, 6, 14),
    )
    assert result.ok is True
    assert result.thresholds_version == "thresholds@2026.09.05"
    assert "thresholds_version=thresholds@2026.09.05" in result.detail


def test_walkforward_gate_payload_accepts_none_pbo_for_single_strategy_run() -> None:
    # 单档案（未跑 CSCV）walk-forward 的 PBO 为 None（经有效 CSCV 验证前为 None，
    # 宪法 §17.7）。build_walkforward_gate_payload 必须容忍 None，不能 `pbo > 0.0`
    # 崩溃（修复前这里会抛 TypeError: '>' not supported between NoneType and float）。
    payload = build_walkforward_gate_payload(
        dsr=0.0,
        pbo=None,
        run_date="2026-06-10",
        start="2023-01-01",
        end="2024-12-31",
        n_periods=12,
        n_variants=1,
    )
    assert payload["pbo"] is None
    assert payload["pbo_valid"] is False
    assert payload["pbo_pass"] is False
    assert payload["both_pass"] is False
    assert payload["cscv_reliable"] is False


def test_walkforward_gate_blocks_when_cscv_variants_below_minimum() -> None:
    # n_variants < MIN_CSCV_VARIANTS(8) 时 PBO 系统性上偏，须 fail-closed，
    # 即使 DSR/PBO 数值本身达标也不放行（恢复 CSCV N>=8 硬前置）。
    payload = build_walkforward_gate_payload(
        dsr=1.9,
        pbo=0.24,
        run_date="2026-06-10",
        start="2023-01-01",
        end="2024-12-31",
        n_periods=12,
        n_variants=5,
    )
    assert payload["both_pass"] is False
    assert payload["cscv_reliable"] is False
    result = validate_walkforward_gate_payload(
        payload,
        today=date(2026, 6, 14),
        heldout_cutoff=date(2024, 12, 31),
    )
    assert result.ok is False
    assert any("n_variants=5 < 8" in item for item in result.blockers)
    assert any("cscv_reliable" in item for item in result.blockers)


def test_walkforward_gate_passes_with_eight_variants() -> None:
    # 恰好 N=8 即满足硬前置（stable_plus profile）。
    result = validate_walkforward_gate_payload(
        _valid_payload(n_variants=8),
        today=date(2026, 6, 14),
        heldout_cutoff=date(2024, 12, 31),
    )
    assert result.ok is True
    assert result.n_variants == 8
    assert result.cscv_reliable is True
