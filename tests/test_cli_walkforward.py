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
