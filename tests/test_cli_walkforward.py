from __future__ import annotations


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
        result = main(["walkforward", "--symbols", "600519", "--start", "2024-01-01", "--end", "2024-06-30"])
        assert result == 0

    def test_min_score_zero_accepts_all(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_walkforward(args):
            assert args.min_score == 0.0
            return 0

        monkeypatch.setattr(cli_mod, "run_walkforward", mock_run_walkforward)
        result = main(["walkforward", "--min-score", "0", "--symbols", "600519", "--start", "2024-01-01", "--end", "2024-06-30"])
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
        result = main(["walkforward", "--cache-path", "/tmp/test_cache.db", "--symbols", "600519", "--start", "2024-01-01", "--end", "2024-06-30"])
        assert result == 0


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

    def test_run_accepts_tdx_vipdoc_source(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_scheduled(args):
            assert args.source == "tdx_vipdoc"
            return 0

        monkeypatch.setattr(cli_mod, "run_scheduled", mock_run_scheduled)
        assert main(["run", "--source", "tdx_vipdoc", "--symbols", "600519"]) == 0

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
        result = main(["walkforward", "--log", str(log_file), "--symbols", "600519", "--start", "2024-01-01", "--end", "2024-06-30"])
        assert result == 0


class TestCLIUpdateYamlParam:
    def test_update_yaml_param_accepted(self, monkeypatch):
        from aqsp.cli import main
        import aqsp.cli as cli_mod

        def mock_run_walkforward(args):
            assert args.update_yaml is True
            return 0

        monkeypatch.setattr(cli_mod, "run_walkforward", mock_run_walkforward)
        result = main(["walkforward", "--update-yaml", "--symbols", "600519", "--start", "2024-01-01", "--end", "2024-06-30"])
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


class TestCLIRegimeDescription:
    def test_known_regimes(self):
        from aqsp.cli import _regime_description

        assert "稳定上涨" in _regime_description("stable_bull")
        assert "波动下跌" in _regime_description("volatile_bear")

    def test_unknown_regime(self):
        from aqsp.cli import _regime_description

        assert "未知" in _regime_description("foobar")
