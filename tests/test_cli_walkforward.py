from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import load_thresholds


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

        monkeypatch.setattr("aqsp.cli.BaostockSource", DummyBaostockSource)
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

        monkeypatch.setattr(cli_mod, "fetch_akshare", boom)

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
        monkeypatch.setattr(
            cli_mod,
            "fetch_akshare",
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

        monkeypatch.setattr(cli_mod, "fetch_akshare", fail)
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

    def test_auto_source_plan_is_local_first_without_cross_tier_consistency(
        self, monkeypatch
    ):
        import aqsp.cli as cli_mod

        class DummySource:
            def __init__(self, *args, **kwargs):
                pass

        monkeypatch.setattr(
            cli_mod,
            "TdxVipdocSource",
            type("Tdx", (DummySource,), {"name": "tdx_vipdoc"}),
        )
        monkeypatch.setattr(
            cli_mod,
            "EastmoneySource",
            type("Em", (DummySource,), {"name": "eastmoney"}),
        )
        monkeypatch.setattr(
            cli_mod, "SinaSource", type("Sina", (DummySource,), {"name": "sina"})
        )
        monkeypatch.setattr(
            cli_mod, "TencentSource", type("Ten", (DummySource,), {"name": "tencent"})
        )
        monkeypatch.setattr(
            cli_mod, "AkshareSource", type("Ak", (DummySource,), {"name": "akshare"})
        )

        source = cli_mod._get_source("auto")

        assert source.primary.name == "tdx_vipdoc"
        assert isinstance(source.primary, cli_mod.SourceFactory)
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
        import aqsp.cli as cli_mod

        monkeypatch.setenv("AQSP_ALLOW_ONLINE_FALLBACK", "false")

        class DummyTdx:
            name = "tdx_vipdoc"

            def __init__(self, *args, **kwargs):
                pass

        monkeypatch.setattr(cli_mod, "TdxVipdocSource", DummyTdx)

        source = cli_mod._get_source("auto")

        assert source.name == "tdx_vipdoc"

    def test_auto_source_plan_reorders_online_fallbacks_by_health(
        self,
        tmp_path,
        monkeypatch,
    ):
        import aqsp.cli as cli_mod

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

        monkeypatch.setattr(
            cli_mod,
            "TdxVipdocSource",
            type("Tdx", (DummySource,), {"name": "tdx_vipdoc"}),
        )
        monkeypatch.setattr(
            cli_mod,
            "EastmoneySource",
            type("Em", (DummySource,), {"name": "eastmoney"}),
        )
        monkeypatch.setattr(
            cli_mod, "SinaSource", type("Sina", (DummySource,), {"name": "sina"})
        )
        monkeypatch.setattr(
            cli_mod, "TencentSource", type("Ten", (DummySource,), {"name": "tencent"})
        )
        monkeypatch.setattr(
            cli_mod, "AkshareSource", type("Ak", (DummySource,), {"name": "akshare"})
        )

        source = cli_mod._get_source("auto")

        assert [item.name for item in source.fallbacks] == [
            "tencent",
            "eastmoney",
            "akshare",
            "sina",
        ]

    def test_auto_source_does_not_require_local_vipdoc_at_construction(
        self, monkeypatch
    ):
        import aqsp.cli as cli_mod

        def fail_if_called():
            raise AssertionError("tdx source should be lazy")

        monkeypatch.setattr(cli_mod, "TdxVipdocSource", fail_if_called)

        source = cli_mod._get_source("auto")

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

        monkeypatch.setattr("aqsp.cli.SqliteDbSource", DummySqliteDbSource)
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
        monkeypatch.setattr("aqsp.cli.SqliteDbSource", DummySqliteDbSource)
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
        assert seen["pool_as_of"] == date.today().isoformat()
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
