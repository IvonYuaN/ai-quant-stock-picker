from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import json

import pandas as pd
import pytest

from aqsp.core.time import today_shanghai
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import load_thresholds


@pytest.fixture(autouse=True)
def _isolate_walkforward_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "aqsp.cli.WALKFORWARD_GATE_PATH",
        str(tmp_path / "data" / "walkforward_gate.json"),
    )


def _make_sample_data(n_days: int = 200) -> pd.DataFrame:
    dates = pd.date_range(end="2024-12-31", periods=n_days, freq="B")
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000_000,
            "amount": 100_000_000,
            "suspended": False,
            "limit_up": 110.0,
            "limit_down": 90.0,
        }
    )


def test_cold_start_counts_observation_only_signal_days(tmp_path) -> None:
    from aqsp.cli import _count_independent_signal_days

    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "signal_date": "2026-06-01",
            "symbol": "600036",
            "thresholds_version": "1.1.1",
            "status": "watch_only",
        },
        {
            "signal_date": "2026-06-02",
            "symbol": "000001",
            "thresholds_version": "1.1.1",
            "status": "not_executable",
        },
        {
            "signal_date": "2026-06-02",
            "symbol": "601318",
            "thresholds_version": "1.1.1",
            "status": "pending",
        },
        {"signal_date": "", "symbol": "bad", "thresholds_version": "1.1.1"},
        {"signal_date": "2026-06-03", "symbol": "legacy_without_thresholds"},
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert _count_independent_signal_days(str(ledger)) == 2


def test_cold_start_counts_runtime_date_aliases(tmp_path) -> None:
    from aqsp.cli import _count_independent_signal_days

    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "signal_day_group": "2026-06-03_ma_pullback",
            "symbol": "600036",
            "status": "watch_only",
        },
        {
            "created_at": "2026-06-04T18:00:00+08:00",
            "symbol": "000001",
            "rating": "watch",
        },
        {
            "date": "2026-06-05",
            "symbol": "601318",
            "score": 51.0,
        },
        {
            "created_at": "2026-06-06T18:00:00+08:00",
            "symbol": "300750",
            "status": "not_executable",
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert _count_independent_signal_days(str(ledger)) == 3


def test_cold_start_counts_no_pick_runtime_days(tmp_path) -> None:
    from aqsp.cli import _count_independent_signal_days

    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "signal_date": "2026-06-10",
            "status": "run_completed_no_picks",
            "thresholds_version": "1.1.1",
        },
        {
            "created_at": "2026-06-11T18:00:00+08:00",
            "status": "run_completed_no_picks",
            "thresholds_version": "1.1.1",
        },
        {
            "signal_date": "2026-06-11",
            "status": "run_completed_no_picks",
            "thresholds_version": "1.1.1",
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert _count_independent_signal_days(str(ledger)) == 2


def test_walkforward_help_handles_percent_text(capsys) -> None:
    from aqsp.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["walkforward", "--help"])

    assert exc_info.value.code == 0
    assert "3.1%硬止损" in capsys.readouterr().out


def test_format_walkforward_pbo_marks_invalid_placeholder() -> None:
    from aqsp.cli import _format_walkforward_pbo

    assert (
        _format_walkforward_pbo(0.0, False) == "0.00%（无效占位，需 grid 多变体 CSCV）"
    )
    assert _format_walkforward_pbo(0.24, True) == "24.00%"


class TestCompositeStrategyInit:
    def test_init_without_config(self):
        strategy = CompositeStrategy()
        assert strategy.name == "composite"
        assert strategy.id == "composite"

    def test_init_with_thresholds(self):
        thresholds = load_thresholds()
        strategy = CompositeStrategy(thresholds=thresholds)
        assert strategy.thresholds is thresholds

    def test_init_with_none_config(self):
        strategy = CompositeStrategy(config=None)
        assert strategy.name == "composite"


class TestCompositeStrategySelectStocks:
    def test_select_stocks_returns_list(self):
        strategy = CompositeStrategy()
        data = {"600519": _make_sample_data(), "000858": _make_sample_data()}
        result = strategy.select_stocks(data, n=5)
        assert isinstance(result, list)

    def test_select_stocks_respects_n(self):
        strategy = CompositeStrategy()
        data = {f"sym{i:03d}": _make_sample_data() for i in range(10)}
        result = strategy.select_stocks(data, n=3)
        assert len(result) <= 3

    def test_select_stocks_empty_data(self):
        strategy = CompositeStrategy()
        result = strategy.select_stocks({}, n=5)
        assert result == []


class TestWalkForwardWithMockData:
    def test_walkforward_runs_without_crash(self):
        from aqsp.backtest.walk_forward import WalkForwardTester

        strategy = CompositeStrategy()
        data = {"600519": _make_sample_data(), "000858": _make_sample_data()}

        tester = WalkForwardTester(
            strategy=strategy,
            train_period_days=60,
            test_period_days=20,
            purge_days=3,
        )

        result = tester.run(data, start_date="2024-01-01", end_date="2024-12-31")
        assert result is not None
        assert hasattr(result, "overall")
        assert hasattr(result, "deflated_sharpe")
        assert hasattr(result, "pbo")

    def test_walkforward_metrics_structure(self):
        from aqsp.backtest.walk_forward import WalkForwardTester

        strategy = CompositeStrategy()
        data = {"600519": _make_sample_data(), "000858": _make_sample_data()}

        tester = WalkForwardTester(
            strategy=strategy,
            train_period_days=60,
            test_period_days=20,
            purge_days=3,
        )

        result = tester.run(data)
        assert isinstance(result.overall.total_return, float)
        assert isinstance(result.overall.sharpe_ratio, float)
        assert isinstance(result.overall.win_rate, float)
        assert isinstance(result.deflated_sharpe, float)
        assert isinstance(result.pbo, float)


class TestCLIFindThresholdsYaml:
    def test_finds_yaml_from_src(self):
        from aqsp.cli import _find_thresholds_yaml

        path = _find_thresholds_yaml()
        assert path is not None
        assert path.exists()
        assert path.name == "thresholds.yaml"


class TestCLIUpdateThresholdsMetadata:
    def test_update_writes_date(self, tmp_path):
        from aqsp.cli import _update_thresholds_metadata

        yaml_content = 'version: "2.0.0"\nlast_walkforward_run: "2025-01-01"\n'
        yaml_file = tmp_path / "thresholds.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        import aqsp.cli as cli_mod

        original = cli_mod._find_thresholds_yaml
        cli_mod._find_thresholds_yaml = lambda: yaml_file
        try:
            ok = _update_thresholds_metadata("2026-05-28")
            assert ok is True
            result = yaml_file.read_text(encoding="utf-8")
            assert 'last_walkforward_run: "2026-05-28"' in result
            assert 'version: "2.0.0"' in result
        finally:
            cli_mod._find_thresholds_yaml = original

    def test_update_returns_false_when_field_missing(self, tmp_path):
        from aqsp.cli import _update_thresholds_metadata

        yaml_content = 'version: "2.0.0"\n'
        yaml_file = tmp_path / "thresholds.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        import aqsp.cli as cli_mod

        original = cli_mod._find_thresholds_yaml
        cli_mod._find_thresholds_yaml = lambda: yaml_file
        try:
            ok = _update_thresholds_metadata("2026-05-28")
            assert ok is False
        finally:
            cli_mod._find_thresholds_yaml = original

    def test_update_returns_false_when_file_not_found(self):
        from aqsp.cli import _update_thresholds_metadata
        import aqsp.cli as cli_mod

        original = cli_mod._find_thresholds_yaml
        cli_mod._find_thresholds_yaml = lambda: None
        try:
            ok = _update_thresholds_metadata("2026-05-28")
            assert ok is False
        finally:
            cli_mod._find_thresholds_yaml = original


class TestCLIMinScoreParam:
    def test_min_score_none_uses_yaml_default(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_walkforward(args):
            return 0

        monkeypatch.setattr(cli_mod, "run_walkforward", mock_run_walkforward)
        result = main(
            [
                "walkforward",
                "--symbols",
                "600519",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        assert result == 0

    def test_min_score_zero_accepts_all(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_walkforward(args):
            assert args.min_score == 0.0
            return 0

        monkeypatch.setattr(cli_mod, "run_walkforward", mock_run_walkforward)
        result = main(
            [
                "walkforward",
                "--min-score",
                "0",
                "--symbols",
                "600519",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        assert result == 0

    def test_min_score_overrides_yaml(self, tmp_path):
        from aqsp.strategies.thresholds import load_thresholds

        thresholds = load_thresholds()
        assert thresholds.composite.min_total_score > 0

    def test_walkforward_parser_reads_engine(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_walkforward(args):
            assert args.engine == "akquant"
            return 0

        monkeypatch.setattr(cli_mod, "run_walkforward", mock_run_walkforward)
        result = main(
            [
                "walkforward",
                "--engine",
                "akquant",
                "--symbols",
                "600519",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        assert result == 0


class TestCLICachePathParam:
    def test_cache_path_param_accepted(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_walkforward(args):
            assert args.cache_path == "/tmp/test_cache.db"
            return 0

        monkeypatch.setattr(cli_mod, "run_walkforward", mock_run_walkforward)
        result = main(
            [
                "walkforward",
                "--cache-path",
                "/tmp/test_cache.db",
                "--symbols",
                "600519",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        assert result == 0


class TestWalkforwardPitEnrichment:
    def test_walkforward_baostock_uses_pit_enrichment(self, monkeypatch, tmp_path):
        from aqsp.cli import main
        from aqsp.data.pit_financial import PitEnrichmentResult

        seen: dict[str, object] = {}

        class DummyBaostockSource:
            def __init__(self, cache=None) -> None:
                self.cache = cache

            def fetch_daily(self, symbols, start, end, adjust=""):
                seen["daily_symbols"] = list(symbols)
                seen["daily_start"] = start.isoformat()
                seen["daily_end"] = end.isoformat()
                dates = pd.date_range(start="2024-01-01", periods=140, freq="B")
                return {
                    "600519": pd.DataFrame(
                        {
                            "date": dates.strftime("%Y-%m-%d"),
                            "symbol": "600519",
                            "name": "贵州茅台",
                            "open": 100.0,
                            "high": 101.0,
                            "low": 99.0,
                            "close": 100.5,
                            "volume": 1_000_000,
                            "amount": 100_000_000,
                            "suspended": False,
                            "limit_up": 110.0,
                            "limit_down": 90.0,
                        }
                    )
                }

        class DummyTester:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def run(self, filtered, start_date=None, end_date=None):
                seen["filtered_symbols"] = list(filtered)
                return SimpleNamespace(
                    overall=SimpleNamespace(
                        total_return=0.1,
                        annual_return=0.12,
                        max_drawdown=0.03,
                        sharpe_ratio=1.2,
                        win_rate=0.55,
                        profit_factor=1.3,
                        trades=10,
                        not_executable=0,
                    ),
                    deflated_sharpe=1.1,
                    pbo=0.2,
                    robustness_score=0.8,
                    parameter_std=0.1,
                    regime_winrates={},
                    periods=[],
                )

        def mock_enrich(frames, symbols, start, end, cache=None):
            seen["pit_symbols"] = list(symbols)
            seen["pit_start"] = start.isoformat()
            seen["pit_end"] = end.isoformat()
            return PitEnrichmentResult(
                frames=frames,
                financial_symbol_count=1,
                disclosure_symbol_count=1,
            )

        monkeypatch.setattr("aqsp.cli._get_source", lambda _name: DummyBaostockSource())
        monkeypatch.setattr(
            "aqsp.data.pit_financial.enrich_ohlcv_with_pit_financials",
            mock_enrich,
        )
        monkeypatch.setattr(
            "aqsp.backtest.walk_forward.WalkForwardTester",
            DummyTester,
        )
        monkeypatch.setattr(
            "aqsp.strategies.composite.CompositeStrategy",
            lambda thresholds=None: object(),
        )

        report_path = tmp_path / "walkforward.md"
        result = main(
            [
                "walkforward",
                "--source",
                "baostock",
                "--symbols",
                "600519",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
                "--report",
                str(report_path),
            ]
        )

        assert result == 0
        assert seen["daily_symbols"] == ["600519"]
        assert seen["pit_symbols"] == ["600519"]
        assert seen["pit_start"] == "2024-01-01"
        assert seen["pit_end"] == "2024-06-30"
        assert seen["filtered_symbols"] == ["600519"]
        assert report_path.exists()


class TestCLIDataSources:
    def test_run_defaults_to_auto_source(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_scheduled(args):
            assert args.source == "auto"
            return 0

        monkeypatch.setattr(cli_mod, "run_scheduled", mock_run_scheduled)
        assert main(["run", "--symbols", "600519"]) == 0

    def test_screen_accepts_local_and_online_source_plans(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        seen = []

        def mock_run_screen(args):
            seen.append(args.source)
            return 0

        monkeypatch.setattr(cli_mod, "run_screen", mock_run_screen)
        assert main(["screen", "--source", "local_first", "--symbols", "600519"]) == 0
        assert main(["screen", "--source", "online_first", "--symbols", "600519"]) == 0
        assert seen == ["local_first", "online_first"]

    def test_screen_accepts_pool_param(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_screen(args):
            assert args.pool == "zz500"
            return 0

        monkeypatch.setattr(cli_mod, "run_screen", mock_run_screen)
        assert main(["screen", "--pool", "zz500"]) == 0

    def test_run_accepts_tdx_vipdoc_source(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_scheduled(args):
            assert args.source == "tdx_vipdoc"
            return 0

        monkeypatch.setattr(cli_mod, "run_scheduled", mock_run_scheduled)
        assert main(["run", "--source", "tdx_vipdoc", "--symbols", "600519"]) == 0

    def test_run_accepts_pool_param(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_scheduled(args):
            assert args.pool == "zz500"
            return 0

        monkeypatch.setattr(cli_mod, "run_scheduled", mock_run_scheduled)
        assert main(["run", "--pool", "zz500"]) == 0

    def test_walkforward_rejects_unwired_tdx_vipdoc_source(self):
        from aqsp.cli import main

        try:
            main(
                [
                    "walkforward",
                    "--source",
                    "tdx_vipdoc",
                    "--symbols",
                    "600519",
                    "--start",
                    "2024-01-01",
                    "--end",
                    "2024-06-30",
                ]
            )
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected argparse rejection")

    def test_fetch_frames_wraps_unexpected_source_errors(self, monkeypatch):
        import aqsp.cli as cli_mod
        from aqsp.core.errors import DataError

        def boom(*args, **kwargs):
            raise RuntimeError("proxy died")

        monkeypatch.setattr(cli_mod, "_get_source", boom)

        try:
            cli_mod._fetch_frames_for_cli("akshare", ["600519"], benchmark_symbol=None)
        except DataError as exc:
            assert "proxy died" in str(exc)
        else:
            raise AssertionError("expected DataError")

    def test_fetch_frames_records_source_health_on_success_and_failure(
        self,
        tmp_path,
        monkeypatch,
    ):
        import aqsp.cli as cli_mod
        from aqsp.core.errors import DataError

        health_path = tmp_path / "source_health.json"
        monkeypatch.setenv("AQSP_SOURCE_HEALTH", str(health_path))

        sample = _make_sample_data()

        class DummySource:
            name = "akshare"

        monkeypatch.setattr(
            cli_mod,
            "_get_source",
            lambda _source_name: DummySource(),
        )
        monkeypatch.setattr(
            cli_mod,
            "fetch_with_source",
            lambda *args, **kwargs: {"600519": sample},
        )

        frames, actual = cli_mod._fetch_frames_for_cli_with_metadata(
            "akshare",
            ["600519"],
            benchmark_symbol=None,
        )
        assert actual == "akshare"
        assert "600519" in frames
        assert '"last_actual_source": "akshare"' in health_path.read_text(
            encoding="utf-8"
        )

        def fail(*args, **kwargs):
            raise DataError("upstream failed")

        monkeypatch.setattr(cli_mod, "fetch_with_source", fail)
        try:
            cli_mod._fetch_frames_for_cli_with_metadata(
                "akshare",
                ["600519"],
                benchmark_symbol=None,
            )
        except DataError as exc:
            assert "upstream failed" in str(exc)
        else:
            raise AssertionError("expected DataError")

        health_text = health_path.read_text(encoding="utf-8")
        assert '"consecutive_failures": 1' in health_text
        assert '"last_requested_source": "akshare"' in health_text

    def test_fetch_frames_for_cli_with_metadata_passes_cache_path_to_source_factory(
        self, monkeypatch, tmp_path
    ):
        import aqsp.cli as cli_mod

        sample = _make_sample_data()
        seen: dict[str, object] = {}
        cache_path = tmp_path / "walkforward_cache.db"

        class DummySource:
            name = "akshare"

        def fake_get_source(source_name, *, cache=None):
            seen["source_name"] = source_name
            seen["cache_path"] = str(cache.db_path) if cache is not None else None
            return DummySource()

        monkeypatch.setattr(cli_mod, "_get_source", fake_get_source)
        monkeypatch.setattr(
            cli_mod,
            "fetch_with_source",
            lambda *args, **kwargs: {"600519": sample},
        )

        frames, actual = cli_mod._fetch_frames_for_cli_with_metadata(
            "akshare",
            ["600519"],
            benchmark_symbol=None,
            cache_path=str(cache_path),
        )

        assert actual == "akshare"
        assert "600519" in frames
        assert seen == {
            "source_name": "akshare",
            "cache_path": str(cache_path),
        }

    def test_auto_source_plan_is_local_first_without_cross_tier_consistency(
        self, monkeypatch
    ):
        from aqsp.data import source_factory as sf
        from aqsp.data.multi_source import SourceFactory

        monkeypatch.delenv("AQSP_SOURCE_HEALTH", raising=False)
        monkeypatch.setattr(
            sf,
            "prioritize_source_ids",
            lambda source_ids, path=None: list(source_ids),
        )

        class DummySource:
            def __init__(self, *args, **kwargs):
                pass

        source = sf.build_data_source(
            "auto",
            overrides={
                "tdx_vipdoc": type("Tdx", (DummySource,), {"name": "tdx_vipdoc"}),
                "eastmoney": type("Em", (DummySource,), {"name": "eastmoney"}),
                "sina": type("Sina", (DummySource,), {"name": "sina"}),
                "tencent": type("Ten", (DummySource,), {"name": "tencent"}),
                "akshare": type("Ak", (DummySource,), {"name": "akshare"}),
            },
        )

        assert source.primary.name == "tdx_vipdoc"
        assert isinstance(source.primary, SourceFactory)
        assert [item.name for item in source.fallbacks] == [
            "eastmoney",
            "sina",
            "tencent",
            "akshare",
        ]
        assert source.validate_consistency is False

    def test_auto_source_plan_becomes_local_only_when_online_fallback_disabled(
        self, monkeypatch
    ):
        from aqsp.data import source_factory as sf

        monkeypatch.setenv("AQSP_ALLOW_ONLINE_FALLBACK", "false")

        class DummyTdx:
            name = "tdx_vipdoc"

            def __init__(self, *args, **kwargs):
                pass

        source = sf.build_data_source("auto", overrides={"tdx_vipdoc": DummyTdx})

        assert source.name == "tdx_vipdoc"

    def test_auto_source_plan_reorders_online_fallbacks_by_health(
        self,
        tmp_path,
        monkeypatch,
    ):
        from aqsp.data import source_factory as sf

        health_path = tmp_path / "source_health.json"
        health_path.write_text(
            """
{
  "sources": {
    "tencent": {"successes": 2, "failures": 0, "last_success": "2026-06-01T10:00:00+08:00"},
    "eastmoney": {"successes": 1, "failures": 0, "last_success": "2026-06-01T09:00:00+08:00"},
    "sina": {"successes": 0, "failures": 2, "last_success": ""},
    "akshare": {"successes": 0, "failures": 0, "last_success": ""}
  }
}
            """.strip(),
            encoding="utf-8",
        )
        monkeypatch.setenv("AQSP_SOURCE_HEALTH", str(health_path))

        class DummySource:
            def __init__(self, *args, **kwargs):
                pass

        source = sf.build_data_source(
            "auto",
            overrides={
                "tdx_vipdoc": type("Tdx", (DummySource,), {"name": "tdx_vipdoc"}),
                "eastmoney": type("Em", (DummySource,), {"name": "eastmoney"}),
                "sina": type("Sina", (DummySource,), {"name": "sina"}),
                "tencent": type("Ten", (DummySource,), {"name": "tencent"}),
                "akshare": type("Ak", (DummySource,), {"name": "akshare"}),
            },
        )

        assert [item.name for item in source.fallbacks] == [
            "tencent",
            "eastmoney",
            "sina",
            "akshare",
        ]

    def test_online_source_plan_keeps_akshare_as_last_supplement(self, monkeypatch):
        from aqsp.data import source_factory as sf

        class DummySource:
            def __init__(self, *args, **kwargs):
                pass

        monkeypatch.setattr(
            sf,
            "prioritize_source_ids",
            lambda source_ids, path=None: ["akshare", "tencent", "eastmoney", "sina"],
        )

        source = sf.build_data_source(
            "online_first",
            overrides={
                "eastmoney": type("Em", (DummySource,), {"name": "eastmoney"}),
                "sina": type("Sina", (DummySource,), {"name": "sina"}),
                "tencent": type("Ten", (DummySource,), {"name": "tencent"}),
                "akshare": type("Ak", (DummySource,), {"name": "akshare"}),
                "tdx_vipdoc": type("Tdx", (DummySource,), {"name": "tdx_vipdoc"}),
            },
        )

        ordered = [source.primary.name] + [item.name for item in source.fallbacks[:-1]]
        assert ordered == ["tencent", "eastmoney", "sina", "akshare"]

    def test_auto_source_does_not_require_local_vipdoc_at_construction(
        self, monkeypatch
    ):
        from aqsp.data import source_factory as sf

        def fail_if_called():
            raise AssertionError("tdx source should be lazy")

        class DummySource:
            def __init__(self, *args, **kwargs):
                pass

        source = sf.build_data_source(
            "auto",
            overrides={
                "tdx_vipdoc": fail_if_called,
                "eastmoney": type("Em", (DummySource,), {"name": "eastmoney"}),
                "sina": type("Sina", (DummySource,), {"name": "sina"}),
                "tencent": type("Ten", (DummySource,), {"name": "tencent"}),
                "akshare": type("Ak", (DummySource,), {"name": "akshare"}),
            },
        )

        assert source.primary.name == "tdx_vipdoc"

    def test_main_returns_nonzero_for_data_error(self, monkeypatch, capsys):
        from aqsp.cli import main
        from aqsp.core.errors import DataError
        import aqsp.cli as cli_mod

        def mock_run_scheduled(args):
            raise DataError("all sources failed")

        monkeypatch.setattr(cli_mod, "run_scheduled", mock_run_scheduled)

        assert main(["run", "--symbols", "600519"]) == 1
        assert "all sources failed" in capsys.readouterr().out

    def test_drop_benchmark_frame_keeps_benchmark_out_of_screening(self):
        from aqsp.cli import _drop_benchmark_frame

        frames = {
            "000300": _make_sample_data(),
            "600519": _make_sample_data(),
        }

        assert set(_drop_benchmark_frame(frames, "000300")) == {"600519"}

    def test_resolve_run_symbols_uses_source_universe_when_no_symbols(
        self, monkeypatch
    ):
        import aqsp.cli as cli_mod
        from aqsp.cli import _resolve_run_symbols

        class SourceWithUniverse:
            name = "source_with_universe"

            def get_available_symbols(self):
                return ["600000", "000001"]

        monkeypatch.setattr(cli_mod, "_get_source", lambda _name: SourceWithUniverse())

        assert _resolve_run_symbols(
            "auto",
            "",
            max_universe=800,
            min_avg_amount=50_000_000,
        ) == ["600000", "000001"]

    def test_resolve_run_symbols_prefers_explicit_symbols(self, monkeypatch):
        import aqsp.cli as cli_mod
        from aqsp.cli import _resolve_run_symbols

        def fail_if_called(_name):
            raise AssertionError("source should not be constructed")

        monkeypatch.setattr(cli_mod, "_get_source", fail_if_called)

        assert _resolve_run_symbols(
            "auto",
            "600519, 300750",
            max_universe=800,
            min_avg_amount=50_000_000,
        ) == [
            "600519",
            "300750",
        ]

    def test_resolve_run_symbols_falls_back_to_default_when_source_build_fails(
        self, monkeypatch
    ):
        from aqsp.cli import _resolve_run_symbols
        from aqsp.core.errors import DataError

        def fail_build(_name):
            raise DataError("tdx vipdoc missing")

        monkeypatch.setattr("aqsp.cli._get_source", fail_build)

        assert _resolve_run_symbols(
            "auto",
            "",
            max_universe=3,
            min_avg_amount=50_000_000,
        ) == ["600519", "300750", "000001"]

    def test_resolve_run_symbols_falls_back_to_default_when_source_universe_errors(
        self, monkeypatch
    ):
        from aqsp.cli import _resolve_run_symbols
        from aqsp.core.errors import DataError

        class SourceWithBrokenUniverse:
            name = "broken"

            def get_available_symbols(self):
                raise DataError("all sources failed")

        monkeypatch.setattr(
            "aqsp.cli._get_source",
            lambda _name: SourceWithBrokenUniverse(),
        )

        assert _resolve_run_symbols(
            "auto",
            "",
            max_universe=2,
            min_avg_amount=50_000_000,
        ) == ["600519", "300750"]


class TestCLILogParam:
    def test_log_param_accepted(self, tmp_path, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        log_file = tmp_path / "test.log"

        def mock_run_walkforward(args):
            assert str(log_file) == args.log
            return 0

        monkeypatch.setattr(cli_mod, "run_walkforward", mock_run_walkforward)
        result = main(
            [
                "walkforward",
                "--log",
                str(log_file),
                "--symbols",
                "600519",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        assert result == 0


class TestCLISymbolsFileParam:
    def test_symbols_file_param_is_used_when_symbols_absent(
        self, tmp_path, monkeypatch
    ):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        symbols_file = tmp_path / "symbols.txt"
        symbols_file.write_text("600519\n300750,000001\n600519\n", encoding="utf-8")

        def mock_run_walkforward(args):
            assert args.symbols_file == str(symbols_file)
            return 0

        monkeypatch.setattr(cli_mod, "run_walkforward", mock_run_walkforward)
        result = main(
            [
                "walkforward",
                "--symbols-file",
                str(symbols_file),
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        assert result == 0

    def test_read_symbols_file_dedupes_and_allows_commas(self, tmp_path):
        from aqsp.cli import _read_symbols_file

        symbols_file = tmp_path / "symbols.txt"
        symbols_file.write_text(
            "600519 # keep comment out\n300750, 000001\n600519\n",
            encoding="utf-8",
        )

        assert _read_symbols_file(symbols_file) == ["600519", "300750", "000001"]


class TestCLIUpdateYamlParam:
    def test_update_yaml_param_accepted(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_walkforward(args):
            assert args.update_yaml is True
            return 0

        monkeypatch.setattr(cli_mod, "run_walkforward", mock_run_walkforward)
        result = main(
            [
                "walkforward",
                "--update-yaml",
                "--symbols",
                "600519",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        assert result == 0


class TestCLIHs300Symbols:
    def test_hs300_has_no_duplicates(self):
        from aqsp.cli import _get_hs300_symbols

        symbols = _get_hs300_symbols()
        assert len(symbols) == len(set(symbols))

    def test_hs300_count(self):
        from aqsp.cli import _get_hs300_symbols

        symbols = _get_hs300_symbols()
        assert len(symbols) >= 200

    def test_hs300_prefers_optional_tushare_constituents(self, monkeypatch):
        from aqsp.cli import _get_hs300_symbols

        monkeypatch.setattr(
            "aqsp.cli.load_optional_index_constituents",
            lambda index_code, as_of: ["300750", "600519"],
        )

        symbols = _get_hs300_symbols(date(2026, 6, 1))
        assert symbols == ["300750", "600519"]


class TestCLIPoolSelection:
    def test_walkforward_pool_uses_universe_pool_symbols(self, monkeypatch, tmp_path):
        from aqsp.cli import main

        seen: dict[str, object] = {}

        class DummyPool:
            def get_symbols(self, as_of=None):
                seen["pool_as_of"] = as_of.isoformat()
                return ["000001", "600519"]

        class DummyTester:
            def __init__(self, **kwargs) -> None:
                pass

            def run(self, filtered, start_date=None, end_date=None):
                seen["filtered_symbols"] = list(filtered)
                return SimpleNamespace(
                    overall=SimpleNamespace(
                        total_return=0.1,
                        annual_return=0.12,
                        max_drawdown=0.03,
                        sharpe_ratio=1.2,
                        win_rate=0.55,
                        profit_factor=1.3,
                        trades=10,
                        not_executable=0,
                    ),
                    deflated_sharpe=1.1,
                    pbo=0.2,
                    robustness_score=0.8,
                    parameter_std=0.1,
                    regime_winrates={},
                    periods=[],
                )

        def mock_fetch_frames(
            source_name, symbols, benchmark_symbol=None, cache_path=None, days=0
        ):
            seen["symbols"] = list(symbols)
            dates = pd.date_range(start="2024-01-01", periods=140, freq="B")
            frame = pd.DataFrame(
                {
                    "date": dates.strftime("%Y-%m-%d"),
                    "symbol": "000001",
                    "name": "平安银行",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10.1,
                    "volume": 1_000_000,
                    "amount": 100_000_000,
                    "suspended": False,
                    "limit_up": 11.0,
                    "limit_down": 9.0,
                }
            )
            return {
                "000001": frame.copy(),
                "600519": frame.assign(symbol="600519", name="贵州茅台"),
            }

        monkeypatch.setattr(
            "aqsp.universe.pool.UniversePool.from_default",
            lambda pool_name: DummyPool(),
        )
        monkeypatch.setattr("aqsp.cli._fetch_frames_for_cli", mock_fetch_frames)
        monkeypatch.setattr("aqsp.backtest.walk_forward.WalkForwardTester", DummyTester)
        monkeypatch.setattr(
            "aqsp.strategies.composite.CompositeStrategy",
            lambda thresholds=None: object(),
        )

        report_path = tmp_path / "walkforward-pool.md"
        result = main(
            [
                "walkforward",
                "--source",
                "multi",
                "--pool",
                "zz500",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
                "--report",
                str(report_path),
            ]
        )

        assert result == 0
        assert seen["pool_as_of"] == "2024-01-01"
        assert seen["symbols"] == ["000001", "600519"]
        assert seen["filtered_symbols"] == ["000001", "600519"]

    def test_walkforward_sqlite_db_bypasses_runtime_cache(self, monkeypatch, tmp_path):
        from aqsp.cli import main

        seen: dict[str, object] = {}

        class DummySqliteDbSource:
            def __init__(self, cache=None) -> None:
                seen["cache"] = cache

            def get_available_symbols(self):
                return ["600519"]

            def fetch_daily(self, symbols, start, end, adjust=""):
                dates = pd.date_range(start="2024-01-01", periods=140, freq="B")
                return {
                    "600519": pd.DataFrame(
                        {
                            "date": dates.strftime("%Y-%m-%d"),
                            "symbol": "600519",
                            "name": "贵州茅台",
                            "open": 100.0,
                            "high": 101.0,
                            "low": 99.0,
                            "close": 100.5,
                            "volume": 1_000_000,
                            "amount": 100_000_000,
                            "suspended": False,
                            "limit_up": 110.0,
                            "limit_down": 90.0,
                        }
                    )
                }

        class DummyTester:
            def __init__(self, **kwargs) -> None:
                pass

            def run(self, filtered, start_date=None, end_date=None):
                return SimpleNamespace(
                    overall=SimpleNamespace(
                        total_return=0.1,
                        annual_return=0.12,
                        max_drawdown=0.03,
                        sharpe_ratio=1.2,
                        win_rate=0.55,
                        profit_factor=1.3,
                        trades=10,
                        not_executable=0,
                    ),
                    deflated_sharpe=1.1,
                    pbo=0.2,
                    robustness_score=0.8,
                    parameter_std=0.1,
                    regime_winrates={},
                    periods=[],
                )

        monkeypatch.setattr(
            "aqsp.cli._build_sqlite_db_source",
            lambda *, cache=None: DummySqliteDbSource(cache=cache),
        )
        monkeypatch.setattr(
            "aqsp.data.pit_financial.enrich_ohlcv_with_pit_financials",
            lambda frames, symbols, start, end, cache=None: SimpleNamespace(
                frames=frames, financial_symbol_count=0, disclosure_symbol_count=0
            ),
        )
        monkeypatch.setattr("aqsp.backtest.walk_forward.WalkForwardTester", DummyTester)
        monkeypatch.setattr(
            "aqsp.strategies.composite.CompositeStrategy",
            lambda thresholds=None: object(),
        )

        report_path = tmp_path / "walkforward-sqlite.md"
        result = main(
            [
                "walkforward",
                "--source",
                "sqlite_db",
                "--symbols",
                "600519",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
                "--report",
                str(report_path),
            ]
        )

        assert result == 0
        assert seen["cache"] is None
        assert report_path.exists()
        report = report_path.read_text(encoding="utf-8")
        assert "## 运行参数" in report
        assert "| source | sqlite_db |" in report
        assert "| horizon_days | 3 |" in report
        assert "| fee_bps | 3 |" in report
        assert "| slippage_bps | 20 |" in report
        assert "| min_score | thresholds.yaml |" in report

    def test_walkforward_uses_env_symbols_when_cli_symbols_missing(
        self, monkeypatch, tmp_path
    ):
        from aqsp.cli import main

        seen: dict[str, object] = {}

        class DummySqliteDbSource:
            def __init__(self, cache=None) -> None:
                pass

            def get_available_symbols(self):
                return ["000915", "000921"]

            def fetch_daily(self, symbols, start, end, adjust=""):
                seen["symbols"] = list(symbols)
                dates = pd.date_range(start="2024-01-01", periods=140, freq="B")
                frame = pd.DataFrame(
                    {
                        "date": dates.strftime("%Y-%m-%d"),
                        "symbol": "000915",
                        "name": "华特达因",
                        "open": 10.0,
                        "high": 10.2,
                        "low": 9.8,
                        "close": 10.1,
                        "volume": 1_000_000,
                        "amount": 100_000_000,
                        "suspended": False,
                        "limit_up": 11.0,
                        "limit_down": 9.0,
                    }
                )
                return {
                    "000915": frame.copy(),
                    "000921": frame.assign(symbol="000921", name="海信家电"),
                }

        class DummyTester:
            def __init__(self, **kwargs) -> None:
                pass

            def run(self, filtered, start_date=None, end_date=None):
                return SimpleNamespace(
                    overall=SimpleNamespace(
                        total_return=0.1,
                        annual_return=0.12,
                        max_drawdown=0.03,
                        sharpe_ratio=1.2,
                        win_rate=0.55,
                        profit_factor=1.3,
                        trades=10,
                        not_executable=0,
                    ),
                    deflated_sharpe=1.1,
                    pbo=0.2,
                    robustness_score=0.8,
                    parameter_std=0.1,
                    regime_winrates={},
                    periods=[],
                )

        monkeypatch.setenv("AQSP_WALKFORWARD_SYMBOLS", "000915,000921")
        monkeypatch.setattr(
            "aqsp.cli._build_sqlite_db_source",
            lambda *, cache=None: DummySqliteDbSource(cache=cache),
        )
        monkeypatch.setattr(
            "aqsp.data.pit_financial.enrich_ohlcv_with_pit_financials",
            lambda frames, symbols, start, end, cache=None: SimpleNamespace(
                frames=frames, financial_symbol_count=0, disclosure_symbol_count=0
            ),
        )
        monkeypatch.setattr("aqsp.backtest.walk_forward.WalkForwardTester", DummyTester)
        monkeypatch.setattr(
            "aqsp.strategies.composite.CompositeStrategy",
            lambda thresholds=None: object(),
        )

        report_path = tmp_path / "walkforward-env-symbols.md"
        result = main(
            [
                "walkforward",
                "--source",
                "sqlite_db",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
                "--report",
                str(report_path),
            ]
        )

        assert result == 0
        assert seen["symbols"] == ["000915", "000921"]
        assert report_path.exists()

    def test_walkforward_sqlite_db_reads_db_path_from_dotenv(
        self, monkeypatch, tmp_path
    ):
        from aqsp.cli import main

        seen: dict[str, object] = {}
        db_path = tmp_path / "market" / "astocks_qfq.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_text("", encoding="utf-8")
        (tmp_path / ".env").write_text(
            f"AQSP_SQLITE_DB_PATH={db_path}\n",
            encoding="utf-8",
        )

        class DummySqliteDbSource:
            def __init__(self, db_path=None, cache=None) -> None:
                seen["db_path"] = str(db_path) if db_path is not None else None
                seen["cache"] = cache

            def get_available_symbols(self):
                return ["600519"]

            def fetch_daily(self, symbols, start, end, adjust=""):
                dates = pd.date_range(start="2024-01-01", periods=140, freq="B")
                return {
                    "600519": pd.DataFrame(
                        {
                            "date": dates.strftime("%Y-%m-%d"),
                            "symbol": "600519",
                            "name": "贵州茅台",
                            "open": 100.0,
                            "high": 101.0,
                            "low": 99.0,
                            "close": 100.5,
                            "volume": 1_000_000,
                            "amount": 100_000_000,
                            "suspended": False,
                            "limit_up": 110.0,
                            "limit_down": 90.0,
                        }
                    )
                }

        class DummyTester:
            def __init__(self, **kwargs) -> None:
                pass

            def run(self, filtered, start_date=None, end_date=None):
                return SimpleNamespace(
                    overall=SimpleNamespace(
                        total_return=0.1,
                        annual_return=0.12,
                        max_drawdown=0.03,
                        sharpe_ratio=1.2,
                        win_rate=0.55,
                        profit_factor=1.3,
                        trades=10,
                        not_executable=0,
                    ),
                    deflated_sharpe=1.1,
                    pbo=0.2,
                    robustness_score=0.8,
                    parameter_std=0.1,
                    regime_winrates={},
                    periods=[],
                )

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AQSP_SQLITE_DB_PATH", raising=False)
        monkeypatch.setattr(
            "aqsp.cli._build_sqlite_db_source",
            lambda *, cache=None: DummySqliteDbSource(db_path=db_path, cache=cache),
        )
        monkeypatch.setattr(
            "aqsp.data.pit_financial.enrich_ohlcv_with_pit_financials",
            lambda frames, symbols, start, end, cache=None: SimpleNamespace(
                frames=frames, financial_symbol_count=0, disclosure_symbol_count=0
            ),
        )
        monkeypatch.setattr("aqsp.backtest.walk_forward.WalkForwardTester", DummyTester)
        monkeypatch.setattr(
            "aqsp.strategies.composite.CompositeStrategy",
            lambda thresholds=None: object(),
        )

        report_path = tmp_path / "walkforward-dotenv-db.md"
        result = main(
            [
                "walkforward",
                "--source",
                "sqlite_db",
                "--symbols",
                "600519",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
                "--report",
                str(report_path),
            ]
        )

        assert result == 0
        assert seen["db_path"] == str(db_path)
        assert seen["cache"] is None
        assert report_path.exists()

    def test_walkforward_sqlite_db_can_skip_pit_financials(self, monkeypatch, tmp_path):
        from aqsp.cli import main

        pit_called = {"value": False}

        class DummySqliteDbSource:
            def __init__(self, db_path=None, cache=None) -> None:
                pass

            def get_available_symbols(self):
                return ["600519"]

            def fetch_daily(self, symbols, start, end, adjust=""):
                dates = pd.date_range(start="2024-01-01", periods=140, freq="B")
                return {
                    "600519": pd.DataFrame(
                        {
                            "date": dates.strftime("%Y-%m-%d"),
                            "symbol": "600519",
                            "name": "贵州茅台",
                            "open": 100.0,
                            "high": 101.0,
                            "low": 99.0,
                            "close": 100.5,
                            "volume": 1_000_000,
                            "amount": 100_000_000,
                            "suspended": False,
                            "limit_up": 110.0,
                            "limit_down": 90.0,
                        }
                    )
                }

        class DummyTester:
            def __init__(self, **kwargs) -> None:
                pass

            def run(self, filtered, start_date=None, end_date=None):
                return SimpleNamespace(
                    overall=SimpleNamespace(
                        total_return=0.1,
                        annual_return=0.12,
                        max_drawdown=0.03,
                        sharpe_ratio=1.2,
                        win_rate=0.55,
                        profit_factor=1.3,
                        trades=10,
                        not_executable=0,
                    ),
                    deflated_sharpe=1.1,
                    pbo=0.2,
                    robustness_score=0.8,
                    parameter_std=0.1,
                    regime_winrates={},
                    periods=[],
                )

        monkeypatch.setattr(
            "aqsp.cli._build_sqlite_db_source",
            lambda *, cache=None: DummySqliteDbSource(cache=cache),
        )
        monkeypatch.setattr(
            "aqsp.data.pit_financial.enrich_ohlcv_with_pit_financials",
            lambda *_args, **_kwargs: pit_called.__setitem__("value", True),
        )
        monkeypatch.setattr("aqsp.backtest.walk_forward.WalkForwardTester", DummyTester)
        monkeypatch.setattr(
            "aqsp.strategies.composite.CompositeStrategy",
            lambda thresholds=None: object(),
        )

        result = main(
            [
                "walkforward",
                "--source",
                "sqlite_db",
                "--symbols",
                "600519",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
                "--report",
                str(tmp_path / "walkforward-skip-pit.md"),
                "--skip-pit-financials",
            ]
        )

        assert result == 0
        assert pit_called["value"] is False

    def test_screen_pool_uses_universe_pool_symbols(self, monkeypatch):
        from aqsp.cli import main

        seen: dict[str, object] = {}

        class DummyPool:
            def get_symbols(self, as_of=None):
                seen["pool_as_of"] = as_of.isoformat()
                return ["000001", "600519"]

        def mock_fetch_frames(
            source_name, symbols, benchmark_symbol=None, cache_path=None, days=0
        ):
            seen["symbols"] = list(symbols)
            latest = "2026-06-01"
            frame = pd.DataFrame(
                [
                    {
                        "date": latest,
                        "symbol": "000001",
                        "name": "平安银行",
                        "open": 10.0,
                        "high": 10.2,
                        "low": 9.8,
                        "close": 10.1,
                        "volume": 1_000_000,
                        "amount": 100_000_000,
                        "suspended": False,
                        "limit_up": 11.0,
                        "limit_down": 9.0,
                    }
                ]
            )
            return {
                "000001": frame.copy(),
                "600519": frame.assign(symbol="600519", name="贵州茅台"),
            }, "akshare"

        monkeypatch.setattr(
            "aqsp.universe.pool.UniversePool.from_default",
            lambda pool_name: DummyPool(),
        )
        monkeypatch.setattr(
            "aqsp.cli._fetch_frames_for_cli_with_metadata",
            mock_fetch_frames,
        )
        monkeypatch.setattr("aqsp.cli.screen_universe", lambda *_args, **_kwargs: [])

        result = main(["screen", "--pool", "zz500"])

        assert result == 0
        assert seen["pool_as_of"] == today_shanghai().isoformat()
        assert seen["symbols"] == ["000001", "600519"]

    def test_main_returns_config_error_when_pool_resolution_fails(self, monkeypatch):
        from aqsp.cli import main

        monkeypatch.setattr(
            "aqsp.universe.pool.UniversePool.from_default",
            lambda pool_name: type(
                "DummyPool",
                (),
                {
                    "get_symbols": staticmethod(
                        lambda as_of=None: (_ for _ in ()).throw(
                            ValueError("Pool zz500 requires TUSHARE_TOKEN")
                        )
                    )
                },
            )(),
        )

        assert main(["screen", "--pool", "zz500"]) == 1


class TestCLIRegimeDescription:
    def test_known_regimes(self):
        from aqsp.cli import _regime_description

        assert "稳定上涨" in _regime_description("stable_bull")
        assert "波动下跌" in _regime_description("volatile_bear")

    def test_unknown_regime(self):
        from aqsp.cli import _regime_description

        assert "未知" in _regime_description("foobar")


def test_walkforward_grid_uses_stable_gate_variants_by_default() -> None:
    import aqsp.cli as cli_mod

    variants = cli_mod._walkforward_grid_variants()

    assert len(variants) == 5
    assert [variant.variant_id for variant in variants] == [
        "WF-001",
        "WF-B01",
        "WF-B02",
        "WF-B04",
        "WF-B08",
    ]
    assert {variant.lookback_days for variant in variants} == {60, 100, 120}
    assert {variant.horizon_days for variant in variants} == {2, 3}
    assert {variant.top_n for variant in variants} == {5, 10, 15, 20}


def test_walkforward_grid_keeps_exploratory_wfb_variants() -> None:
    import aqsp.cli as cli_mod

    variants = cli_mod._walkforward_grid_variants("exploratory")

    assert len(variants) == 11
    assert [variant.variant_id for variant in variants] == [
        "WF-001",
        "WF-B01",
        "WF-B02",
        "WF-B03",
        "WF-B04",
        "WF-B05",
        "WF-B06",
        "WF-B07",
        "WF-B08",
        "WF-B09",
        "WF-B10",
    ]
    assert {variant.lookback_days for variant in variants} == {20, 40, 60, 80, 100, 120}
    assert {variant.horizon_days for variant in variants} == {1, 2, 3, 5, 7, 10}
    assert {variant.top_n for variant in variants} == {5, 10, 15, 20}


def test_walkforward_sqlite_prefiltered_symbols_skip_duplicate_coverage_check(
    monkeypatch,
) -> None:
    from aqsp.services.walkforward_data import (
        WalkforwardFetchRequest,
        fetch_walkforward_frames,
    )

    class DummySqliteSource:
        def __init__(self) -> None:
            self.coverage_calls = 0

        def get_available_symbols(self):
            return ["600519", "300750"]

        def get_symbols_with_daily_coverage(self, symbols, start, end, min_rows=None):
            self.coverage_calls += 1
            return symbols

        def fetch_daily(self, symbols, start, end, adjust=""):
            frame = pd.DataFrame(
                {
                    "date": ["2024-01-02"],
                    "open": [1.0],
                    "high": [1.0],
                    "low": [1.0],
                    "close": [1.0],
                    "volume": [1.0],
                    "amount": [1.0],
                    "symbol": ["600519"],
                    "name": ["demo"],
                }
            )
            return {symbol: frame for symbol in symbols}

    dummy = DummySqliteSource()
    monkeypatch.setenv("AQSP_SQLITE_PREFILTERED_SYMBOLS", "1")

    result = fetch_walkforward_frames(
        WalkforwardFetchRequest(
            source="sqlite_db",
            symbols=["600519", "300750"],
            start="2024-01-01",
            end="2024-01-31",
            cache_path=None,
            skip_pit_financials=True,
        ),
        get_source_fn=lambda _source: dummy,
        fetch_frames_for_cli_fn=lambda *args, **kwargs: {},
        load_csv_fn=lambda _source: {},
        fetch_days_fn=lambda *_args: 20,
        print_fn=lambda *_args: None,
    )

    assert sorted(result.symbols) == ["300750", "600519"]
    assert dummy.coverage_calls == 0


def test_walkforward_sqlite_main_passes_cache_path_to_sqlite_source(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}
    cache_path = tmp_path / "walkforward-cache.db"

    class DummySource:
        def get_available_symbols(self):
            return ["600519"]

        def fetch_daily(self, symbols, start, end, adjust=""):
            dates = pd.date_range(start="2024-01-01", periods=140, freq="B")
            return {
                "600519": pd.DataFrame(
                    {
                        "date": dates.strftime("%Y-%m-%d"),
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1_000_000,
                        "amount": 100_000_000,
                        "suspended": False,
                        "limit_up": 110.0,
                        "limit_down": 90.0,
                    }
                )
            }

    class DummyTester:
        def __init__(self, **kwargs) -> None:
            pass

        def run(self, filtered, start_date=None, end_date=None):
            return SimpleNamespace(
                overall=SimpleNamespace(
                    total_return=0.1,
                    annual_return=0.12,
                    max_drawdown=0.03,
                    sharpe_ratio=1.2,
                    win_rate=0.55,
                    profit_factor=1.3,
                    trades=10,
                    not_executable=0,
                ),
                deflated_sharpe=1.1,
                pbo=0.2,
                robustness_score=0.8,
                parameter_std=0.1,
                regime_winrates={},
                periods=[],
            )

    def fake_build_sqlite_db_source(*, cache=None):
        seen["cache_path"] = str(cache.db_path) if cache is not None else None
        return DummySource()

    monkeypatch.setattr(cli_mod, "_build_sqlite_db_source", fake_build_sqlite_db_source)
    monkeypatch.setattr(
        "aqsp.data.pit_financial.enrich_ohlcv_with_pit_financials",
        lambda frames, symbols, start, end, cache=None: SimpleNamespace(
            frames=frames, financial_symbol_count=0, disclosure_symbol_count=0
        ),
    )
    monkeypatch.setattr("aqsp.backtest.walk_forward.WalkForwardTester", DummyTester)
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda thresholds=None: object(),
    )

    report_path = tmp_path / "walkforward-cache-pass.md"
    result = cli_mod.main(
        [
            "walkforward",
            "--source",
            "sqlite_db",
            "--symbols",
            "600519",
            "--start",
            "2024-01-01",
            "--end",
            "2024-06-30",
            "--cache-path",
            str(cache_path),
            "--skip-pit-financials",
            "--report",
            str(report_path),
        ]
    )

    assert result == 0
    assert seen == {"cache_path": str(cache_path)}


def test_walkforward_defaults_to_recent_window_dates(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, str] = {}

    class DummySource:
        def get_available_symbols(self):
            return ["600519"]

        def fetch_daily(self, symbols, start, end, adjust=""):
            seen["start"] = start.isoformat()
            seen["end"] = end.isoformat()
            dates = pd.date_range(start="2023-06-22", periods=260, freq="B")
            return {
                "600519": pd.DataFrame(
                    {
                        "date": dates.strftime("%Y-%m-%d"),
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1_000_000,
                        "amount": 100_000_000,
                        "suspended": False,
                        "limit_up": 110.0,
                        "limit_down": 90.0,
                    }
                )
            }

    class DummyEngine:
        def run(self, strategy, filtered, start_date=None, end_date=None, config=None):
            seen["run_start"] = str(start_date)
            seen["run_end"] = str(end_date)
            return SimpleNamespace(
                overall=SimpleNamespace(
                    total_return=0.1,
                    annual_return=0.12,
                    max_drawdown=0.03,
                    sharpe_ratio=1.2,
                    win_rate=0.55,
                    profit_factor=1.3,
                    trades=10,
                    not_executable=0,
                ),
                deflated_sharpe=1.1,
                pbo=0.2,
                robustness_score=0.8,
                parameter_std=0.1,
                regime_winrates={},
                periods=[],
            )

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 20))
    monkeypatch.setattr(
        cli_mod,
        "_build_sqlite_db_source",
        lambda *, cache=None: DummySource(),
    )
    monkeypatch.setattr(
        "aqsp.data.pit_financial.enrich_ohlcv_with_pit_financials",
        lambda frames, symbols, start, end, cache=None: SimpleNamespace(
            frames=frames, financial_symbol_count=0, disclosure_symbol_count=0
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "resolve_walkforward_engine",
        lambda _requested: (
            DummyEngine(),
            SimpleNamespace(
                requested="auto",
                resolved="builtin",
                mode="native",
                message="ok",
            ),
        ),
    )
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda thresholds=None: object(),
    )

    report_path = tmp_path / "walkforward-default-window.md"
    result = cli_mod.main(
        [
            "walkforward",
            "--source",
            "sqlite_db",
            "--symbols",
            "600519",
            "--skip-pit-financials",
            "--report",
            str(report_path),
        ]
    )

    assert result == 0
    assert seen["start"] == "2023-06-21"
    assert seen["end"] == "2026-06-20"
    assert seen["run_start"] == "2023-06-21"
    assert seen["run_end"] == "2026-06-20"


def test_sqlite_fetch_daily_skips_duplicate_coverage_check_when_prefiltered(
    monkeypatch, tmp_path
) -> None:
    from aqsp.data.sqlite_db_source import SqliteDbSource

    db = tmp_path / "astocks_raw.db"
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stocks(ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq(
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                close_qfq REAL,
                volume INTEGER,
                amount REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO stocks(ts_code, name) VALUES('600519.SH', 'demo')"
        )
        for trade_date in ("20240102", "20240115", "20240131"):
            conn.execute(
                """
                INSERT INTO daily_qfq(
                    ts_code, trade_date, open, high, low, close, close_qfq, volume, amount
                ) VALUES('600519.SH', ?, 10, 11, 9, 10, 10, 1000, 10000)
                """,
                (trade_date,),
            )
        conn.commit()

    source = SqliteDbSource(db_path=db, cache=None)

    def fail_coverage(*_args, **_kwargs):
        raise AssertionError("duplicate coverage check should be skipped")

    monkeypatch.setattr(source, "get_symbols_with_daily_coverage", fail_coverage)
    monkeypatch.setenv("AQSP_SQLITE_PREFILTERED_SYMBOLS", "1")

    out = source.fetch_daily(
        ["600519"], start=date(2024, 1, 1), end=date(2024, 1, 31), adjust=""
    )

    assert "600519" in out


def test_sqlite_fetch_daily_reuses_service_prefilter_snapshot_without_env(
    monkeypatch, tmp_path
) -> None:
    from aqsp.data.sqlite_db_source import SqliteDbSource

    db = tmp_path / "astocks_raw.db"
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stocks(ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq(
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                close_qfq REAL,
                volume INTEGER,
                amount REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO stocks(ts_code, name) VALUES('600519.SH', 'demo')"
        )
        for trade_date in ("20240102", "20240115", "20240131"):
            conn.execute(
                """
                INSERT INTO daily_qfq(
                    ts_code, trade_date, open, high, low, close, close_qfq, volume, amount
                ) VALUES('600519.SH', ?, 10, 11, 9, 10, 10, 1000, 10000)
                """,
                (trade_date,),
            )
        conn.commit()

    source = SqliteDbSource(db_path=db, cache=None)
    covered = source.get_symbols_with_daily_coverage(
        ["600519"],
        date(2024, 1, 1),
        date(2024, 1, 31),
        min_rows=None,
    )
    assert covered == ["600519"]

    def fail_coverage(*_args, **_kwargs):
        raise AssertionError("service-prefiltered coverage should be reused")

    monkeypatch.setattr(source, "get_symbols_with_daily_coverage", fail_coverage)

    out = source.fetch_daily(
        ["600519"], start=date(2024, 1, 1), end=date(2024, 1, 31), adjust=""
    )

    assert "600519" in out


def test_walkforward_grid_cscv_writes_valid_pbo_gate(monkeypatch, tmp_path):
    import aqsp.cli as cli_mod
    from aqsp.backtest.walk_forward import BacktestResult
    from aqsp.research_engine import EngineResolution
    from aqsp.walkforward_gate import validate_walkforward_gate_payload

    default_gate_path = tmp_path / "default_gate.json"
    gate_path = tmp_path / "custom_gate.json"
    monkeypatch.setattr(cli_mod, "WALKFORWARD_GATE_PATH", str(default_gate_path))
    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 20))
    monkeypatch.setattr(
        cli_mod,
        "_get_hs300_symbols",
        lambda as_of: ["600519"],
    )

    dates = pd.date_range(start="2024-01-01", periods=160, freq="B")

    class DummySource:
        def get_available_symbols(self):
            return ["600519"]

        def fetch_daily(self, symbols, start, end, adjust=""):
            return {
                "600519": pd.DataFrame(
                    {
                        "date": dates.strftime("%Y-%m-%d"),
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1_000_000,
                        "amount": 100_000_000,
                        "suspended": False,
                        "limit_up": 110.0,
                        "limit_down": 90.0,
                    }
                )
            }

    class DummyEngine:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, strategy, data, *, start_date=None, end_date=None, config):
            self.calls += 1
            variant_idx = self.calls - 2
            if self.calls == 1:
                returns = [0.01, 0.02, 0.01, 0.03]
                sharpe = 1.0
            else:
                returns = [
                    0.01 * (variant_idx + 1),
                    -0.004 * (variant_idx % 3),
                    0.006 * ((variant_idx % 4) + 1),
                    0.002 * (variant_idx + 2),
                    -0.003 * (variant_idx % 2),
                    0.004 * ((variant_idx % 5) + 1),
                    0.001 * (variant_idx + 1),
                    -0.002 * (variant_idx % 4),
                    0.003 * ((variant_idx % 3) + 1),
                    0.002 * ((variant_idx % 6) + 1),
                ]
                sharpe = 2.0 + variant_idx * 0.1
            periods = [
                BacktestResult(
                    period=f"{dates[i].date()} to {dates[i + 1].date()}",
                    total_return=value,
                    annual_return=value,
                    max_drawdown=0.01,
                    sharpe_ratio=sharpe,
                    win_rate=0.5,
                    profit_factor=1.2,
                    trades=1,
                    not_executable=0,
                )
                for i, value in enumerate(returns)
            ]
            return SimpleNamespace(
                overall=SimpleNamespace(
                    total_return=sum(returns),
                    annual_return=sum(returns),
                    max_drawdown=0.03,
                    sharpe_ratio=sharpe,
                    win_rate=0.55,
                    profit_factor=1.3,
                    trades=len(returns),
                    not_executable=0,
                ),
                deflated_sharpe=0.0,
                pbo=0.0,
                robustness_score=0.8,
                parameter_std=0.1,
                regime_winrates={},
                periods=periods,
            )

    engine = DummyEngine()
    monkeypatch.setattr(
        cli_mod, "_build_sqlite_db_source", lambda cache=None: DummySource()
    )
    monkeypatch.setattr(
        cli_mod,
        "resolve_walkforward_engine",
        lambda requested: (
            engine,
            EngineResolution(
                requested="builtin",
                resolved="builtin",
                mode="test",
                message="test engine",
            ),
        ),
    )
    monkeypatch.setattr(
        "aqsp.data.pit_financial.enrich_ohlcv_with_pit_financials",
        lambda frames, symbols, start, end, cache=None: SimpleNamespace(
            frames=frames, financial_symbol_count=0, disclosure_symbol_count=0
        ),
    )
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda thresholds=None: object(),
    )

    report = tmp_path / "walkforward-grid.md"
    result = cli_mod.main(
        [
            "walkforward",
            "--grid-cscv",
            "--source",
            "sqlite_db",
            "--symbols",
            "600519",
            "--start",
            "2024-01-01",
            "--end",
            "2024-08-01",
            "--report",
            str(report),
            "--gate-path",
            str(gate_path),
        ]
    )

    assert result == 0
    assert gate_path.exists()
    assert not default_gate_path.exists()
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    validation = validate_walkforward_gate_payload(
        payload,
        today=date(2026, 6, 20),
    )
    assert payload["pbo"] > 0.0
    assert payload["pbo_valid"] is True
    assert payload["n_periods"] == 10
    assert payload["source"] == "sqlite_db"
    assert "price_mode" in payload
    assert validation.pbo_valid is True
    report_text = report.read_text(encoding="utf-8")
    assert "## 多变体 CSCV" in report_text
    assert "CSCV 组合数" in report_text
    assert "λ<=0 组合数" in report_text
    assert "### PBO 失败定位" in report_text
    assert "CSCV 失败组合占比" in report_text
    assert "最差对齐周期" in report_text
    assert "最优变体" in report_text
    assert "全池平均收益" in report_text
    assert "全池下跌占比" in report_text
    assert "训练选中变体" in report_text
    assert "测试最优变体" in report_text
    assert (
        "| 变体 | mom | tr | lb | h | top | Sharpe | 总收益 | 周期数 |" in report_text
    )
    assert "| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 |" in report_text


def test_walkforward_grid_dsr_uses_period_level_observation_count(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    captured: dict[str, int] = {}

    class DummyEngine:
        def run(self, strategy, data, *, start_date=None, end_date=None, config=None):
            raise AssertionError("engine.run should be monkeypatched per variant result")

    variant_results = [
        SimpleNamespace(
            overall=SimpleNamespace(sharpe_ratio=1.2, total_return=0.08, trades=40),
            periods=[
                SimpleNamespace(period="p1", total_return=0.01),
                SimpleNamespace(period="p2", total_return=0.02),
                SimpleNamespace(period="p3", total_return=0.03),
                SimpleNamespace(period="p4", total_return=0.04),
            ],
        ),
        SimpleNamespace(
            overall=SimpleNamespace(sharpe_ratio=2.0, total_return=0.1, trades=55),
            periods=[
                SimpleNamespace(period="p1", total_return=0.01),
                SimpleNamespace(period="p2", total_return=0.02),
                SimpleNamespace(period="p3", total_return=0.03),
                SimpleNamespace(period="p4", total_return=0.04),
            ],
        ),
    ]

    variants = (
        cli_mod.WalkForwardGridVariant("A", 0.3, 0.3, 60, 3, 10),
        cli_mod.WalkForwardGridVariant("B", 0.4, 0.2, 40, 5, 10),
    )
    run_calls = {"count": 0}

    def fake_engine_run(*_args, **_kwargs):
        idx = run_calls["count"]
        run_calls["count"] += 1
        return variant_results[idx]

    monkeypatch.setattr(DummyEngine, "run", fake_engine_run)
    monkeypatch.setattr(cli_mod, "_walkforward_grid_variants", lambda _profile: variants)
    monkeypatch.setattr(cli_mod, "_apply_walkforward_grid_variant", lambda thresholds, variant: thresholds)
    monkeypatch.setattr(
        "aqsp.cli._execution_cost_bps_from_thresholds",
        lambda thresholds: (3.0, 20.0),
    )
    monkeypatch.setattr(
        "aqsp.backtest.walk_forward.WalkForwardTester.calculate_cscv_pbo",
        lambda returns_matrix, s=2: (
            0.25,
            {"n_combos": 1, "n_lambda_le_0": 0, "lambda_median": 0.1, "lambda_mean": 0.1, "s": s, "block_size": 2},
        ),
    )
    monkeypatch.setattr(
        "aqsp.backtest.walk_forward.WalkForwardTester._calculate_deflated_sharpe",
        lambda sharpe, n_trials, n_obs, **_kwargs: captured.setdefault("n_obs", n_obs) or 1.23,
    )

    dsr, pbo, min_periods, _rows, _details = cli_mod._run_walkforward_grid_cscv(
        engine=DummyEngine(),
        filtered={"600519": pd.DataFrame()},
        thresholds=load_thresholds(),
        args=SimpleNamespace(
            grid_profile="stable",
            start="2024-01-01",
            end="2024-08-01",
        ),
        base_train_days=120,
        base_test_days=20,
        base_purge_days=5,
        base_tiered_stop=False,
    )

    assert pbo == 0.25
    assert min_periods == 4
    assert run_calls["count"] == 2
    assert captured["n_obs"] == 55
