from __future__ import annotations

import argparse
import json
import logging
import pandas as pd
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from aqsp.presentation import (
    format_review_meta,
    format_watch_review_action,
    format_watch_review_line,
    normalize_research_tone,
    review_priority_label,
)
from aqsp.core.errors import DataError, FreshnessError
from aqsp.core.time import now_shanghai, today_shanghai


@dataclass(frozen=True)
class PipelineConfig:
    project_root: Path
    source: str
    mode: str
    limit: int
    max_universe: int
    min_avg_amount: float
    max_data_lag_days: int
    enable_online_factors: bool
    allow_online_fallback: bool
    ledger_path: str
    report_path: str
    csv_path: str
    briefing_path: str
    paper_report_path: str
    dashboard_html: str
    dashboard_db: str
    paper_ledger: str
    closing_review_path: str
    notify: bool
    notify_mode: str
    dry_run: bool
    enable_debate: bool
    enable_auto_evolution: bool


@dataclass
class StepResult:
    name: str
    success: bool
    duration_seconds: float
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    started_at: str
    finished_at: str
    duration_seconds: float
    steps: list[StepResult]
    overall_success: bool
    summary: str


def _setup_logging(verbose: bool, log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    today_str = today_shanghai().isoformat()
    log_file = log_dir / f"{today_str}.log"

    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(fmt))

    logger = logging.getLogger("aqsp.pipeline")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def _is_trade_day(d: date) -> bool:
    from aqsp.core.time import is_trading_day

    return is_trading_day(d)


def _run_step(
    name: str,
    fn: Any,
    logger: logging.Logger,
    dry_run: bool = False,
) -> StepResult:
    import time

    logger.info("▶ 开始: %s", name)
    start = time.monotonic()
    try:
        if dry_run:
            logger.info("  [dry-run] 跳过实际执行")
            elapsed = time.monotonic() - start
            return StepResult(
                name=name,
                success=True,
                duration_seconds=elapsed,
                message="dry-run 跳过",
            )

        result = fn()
        elapsed = time.monotonic() - start
        if isinstance(result, dict):
            exit_code = result.get("exit_code")
            if isinstance(exit_code, int) and exit_code != 0:
                message = str(result.get("error") or f"exit_code={exit_code}")
                logger.error("✗ 失败: %s - %s", name, message)
                return StepResult(
                    name=name,
                    success=False,
                    duration_seconds=elapsed,
                    message=message,
                    details=result,
                )
        logger.info("✓ 完成步骤: %s (%.1fs)", name, elapsed)
        if isinstance(result, dict):
            return StepResult(
                name=name, success=True, duration_seconds=elapsed, details=result
            )
        return StepResult(name=name, success=True, duration_seconds=elapsed)

    except (DataError, FreshnessError) as exc:
        elapsed = time.monotonic() - start
        logger.error("✗ 数据错误: %s - %s", name, exc)
        return StepResult(
            name=name,
            success=False,
            duration_seconds=elapsed,
            message=f"数据错误: {exc}",
        )

    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.error("✗ 失败: %s - %s", name, exc)
        logger.debug(traceback.format_exc())
        return StepResult(
            name=name, success=False, duration_seconds=elapsed, message=str(exc)
        )


def _step_update_data(config: PipelineConfig, logger: logging.Logger) -> dict[str, Any]:
    from aqsp.data import fetch_with_source

    logger.info("  拉取最新行情数据 (source=%s)", config.source)

    source = _build_data_source(config)
    symbols = _resolve_symbols(config, logger)
    frames = fetch_with_source(source, symbols, days=260)

    fresh_count = sum(1 for df in frames.values() if df is not None and not df.empty)
    logger.info("  获取到 %d 只标的数据", fresh_count)

    from aqsp.data.cache import DataCache

    cache = DataCache()
    cleared = cache.clear_expired(max_age_hours=168)
    if cleared > 0:
        logger.info("  清理过期缓存: %d 条", cleared)

    return {"symbol_count": fresh_count, "cache_cleared": cleared}


def _build_data_source(config: PipelineConfig) -> Any:
    from aqsp.data.multi_source import MultiSource, SourceFactory
    from aqsp.data.eastmoney_source import EastmoneySource
    from aqsp.data.sina_source import SinaSource
    from aqsp.data.tencent_source import TencentSource
    from aqsp.data.akshare_source import AkshareSource
    from aqsp.data.tdx_vipdoc_source import TdxVipdocSource

    if config.source in ("auto", "local_first", "multi"):
        if config.allow_online_fallback:
            from aqsp.data.cache import DataCache

            cache = DataCache()
            sources = [
                EastmoneySource(cache=cache),
                SinaSource(cache=cache),
                TencentSource(cache=cache),
                AkshareSource(cache=cache),
            ]
            source = MultiSource(
                SourceFactory("tdx_vipdoc", TdxVipdocSource),
                sources,
                validate_consistency=False,
            )
        else:
            source = TdxVipdocSource()
    else:
        from aqsp.cli import _get_source

        source = _get_source(config.source)
    return source


def _build_resilient_history_source(config: PipelineConfig) -> Any:
    from aqsp.data.cache import DataCache
    from aqsp.data.multi_source import MultiSource, SourceFactory
    from aqsp.data.eastmoney_source import EastmoneySource
    from aqsp.data.sina_source import SinaSource
    from aqsp.data.tencent_source import TencentSource
    from aqsp.data.akshare_source import AkshareSource
    from aqsp.data.tdx_vipdoc_source import TdxVipdocSource

    cache = DataCache()
    if config.source in {"auto", "local_first"}:
        return MultiSource(
            SourceFactory("tdx_vipdoc", TdxVipdocSource),
            [
                EastmoneySource(cache=cache),
                SinaSource(cache=cache),
                TencentSource(cache=cache),
                AkshareSource(cache=cache),
            ],
            validate_consistency=False,
        )
    return MultiSource(
        EastmoneySource(cache=cache),
        [
            SinaSource(cache=cache),
            TencentSource(cache=cache),
            AkshareSource(cache=cache),
            SourceFactory("tdx_vipdoc", TdxVipdocSource),
        ],
        validate_consistency=False,
    )


def _fetch_history_frames_resilient(
    config: PipelineConfig,
    symbols: list[str],
    *,
    days: int,
    logger: logging.Logger,
    benchmark_symbols: list[str] | None = None,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    from aqsp.data import fetch_with_source

    frames: dict[str, pd.DataFrame] = {}
    attempted_sources = [config.source]
    if symbols or benchmark_symbols:
        try:
            source = _build_data_source(config)
            primary_benchmark = benchmark_symbols[0] if benchmark_symbols else None
            frames = fetch_with_source(
                source,
                symbols,
                days=days,
                benchmark_symbol=primary_benchmark,
            )
            missing_benchmarks = [
                symbol for symbol in (benchmark_symbols or []) if symbol not in frames
            ]
            if missing_benchmarks:
                end = today_shanghai()
                start = end - timedelta(days=max(days * 2, 120))
                frames.update(source.fetch_index(missing_benchmarks, start, end))
        except Exception as exc:
            logger.warning("  主数据源历史取数失败，准备兜底: %s", exc)
            frames = {}

    missing_symbols = [symbol for symbol in symbols if symbol not in frames]
    missing_benchmarks = [
        symbol for symbol in (benchmark_symbols or []) if symbol not in frames
    ]
    if not missing_symbols and not missing_benchmarks:
        return frames, attempted_sources

    attempted_sources.append("resilient_history")
    try:
        fallback_source = _build_resilient_history_source(config)
        before = len(frames)
        if missing_symbols:
            frames.update(
                fetch_with_source(fallback_source, missing_symbols, days=days)
            )
        if missing_benchmarks:
            end = today_shanghai()
            start = end - timedelta(days=max(days * 2, 120))
            frames.update(fallback_source.fetch_index(missing_benchmarks, start, end))
        logger.info(
            "  历史取数兜底完成: recovered=%d still_missing=%d",
            max(0, len(frames) - before),
            len([symbol for symbol in missing_symbols if symbol not in frames])
            + len([symbol for symbol in missing_benchmarks if symbol not in frames]),
        )
    except Exception as exc:
        logger.warning("  兜底历史取数失败: %s", exc)
    return frames, attempted_sources


def _resolve_symbols(config: PipelineConfig, logger: logging.Logger) -> list[str]:
    from aqsp.universe import DEFAULT_SYMBOLS

    symbols_str = ""
    try:
        from aqsp.config import load_runtime_config

        env = load_runtime_config()
        symbols_str = ",".join(env.symbols)
    except Exception:
        pass

    if symbols_str:
        return [s.strip() for s in symbols_str.split(",") if s.strip()]

    try:
        from aqsp.cli import _get_source

        if not config.allow_online_fallback and config.source in {
            "auto",
            "local_first",
        }:
            from aqsp.data.tdx_vipdoc_source import TdxVipdocSource

            source = TdxVipdocSource()
        else:
            source = _get_source(config.source)
        if hasattr(source, "get_liquid_symbols"):
            liquid = source.get_liquid_symbols(
                limit=config.max_universe,
                min_amount=config.min_avg_amount,
            )
            if liquid:
                logger.info("  自动选取 %d 只流动性标的", len(liquid))
                return liquid
        if hasattr(source, "get_available_symbols"):
            available = source.get_available_symbols()
            if available:
                return available[: config.max_universe]
    except Exception as exc:
        logger.warning("  自动选取标的失败, 使用默认池: %s", exc)

    return list(DEFAULT_SYMBOLS)


def _step_run_strategy(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  执行选股策略 (mode=%s, limit=%d)", config.mode, config.limit)

    argv = [
        "run",
        "--source",
        config.source,
        "--mode",
        config.mode,
        "--limit",
        str(config.limit),
        "--max-universe",
        str(config.max_universe),
        "--min-avg-amount",
        str(config.min_avg_amount),
        "--max-data-lag-days",
        str(config.max_data_lag_days),
        "--benchmark-symbol",
        "",
        "--ledger",
        config.ledger_path,
        "--report",
        config.report_path,
        "--output-csv",
        config.csv_path,
    ]
    if _notify_fanout_enabled(config):
        argv.append("--notify")
    if config.enable_debate:
        argv.append("--enable-debate")
    if config.enable_online_factors:
        argv.append("--enable-online-factors")

    from aqsp.cli import main

    exit_code = main(argv)

    if exit_code == 2:
        logger.warning("  策略运行完成但熔断器触发 (exit_code=2)")
        return {"exit_code": 2, "circuit_breaker": True}
    if exit_code != 0:
        raise DataError(f"策略运行失败, exit_code={exit_code}")

    report_path = Path(config.project_root / config.report_path)
    report_size = report_path.stat().st_size if report_path.exists() else 0
    logger.info("  报告已生成: %s (%d bytes)", config.report_path, report_size)

    return {"exit_code": 0, "report_size": report_size}


def _step_morning_breakout(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  执行早盘打板策略")
    symbols = _resolve_symbols(config, logger)

    argv = [
        "morning-breakout",
        "--source",
        config.source,
        "--symbols",
        ",".join(symbols),
        "--pool",
        "sh300",
        "--top",
        "5",
    ]
    if _notify_fanout_enabled(config):
        argv.append("--notify")

    from aqsp.cli import main

    exit_code = main(argv)

    return {"exit_code": exit_code}


def _step_closing_premium(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  执行尾盘溢价策略")
    symbols = _resolve_symbols(config, logger)

    argv = [
        "closing-premium",
        "--source",
        config.source,
        "--symbols",
        ",".join(symbols),
        "--pool",
        "sh300",
        "--top",
        "5",
    ]
    if _notify_fanout_enabled(config):
        argv.append("--notify")

    from aqsp.cli import main

    exit_code = main(argv)

    return {"exit_code": exit_code}


def _step_closing_review(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  执行收盘复盘")
    report_date = today_shanghai().isoformat()
    latest_output = config.project_root / config.closing_review_path
    dated_output = latest_output.with_name(f"closing_review-{report_date}.md")

    argv = [
        "closing-review",
        "--date",
        report_date,
        "--output",
        config.closing_review_path,
    ]
    if _notify_fanout_enabled(config):
        argv.append("--notify")

    from aqsp.cli import main

    exit_code = main(argv)

    if exit_code == 0 and latest_output.exists():
        dated_output.parent.mkdir(parents=True, exist_ok=True)
        dated_output.write_text(
            latest_output.read_text(encoding="utf-8"), encoding="utf-8"
        )

    return {
        "exit_code": exit_code,
        "report_path": config.closing_review_path,
        "dated_report_path": str(dated_output.relative_to(config.project_root)),
        "report_size": latest_output.stat().st_size if latest_output.exists() else 0,
    }


def _step_validate_predictions(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  验证历史预测结果")

    ledger_path = config.project_root / config.ledger_path
    if not ledger_path.exists():
        logger.info("  Ledger 文件不存在, 跳过验证")
        return {"checked": 0, "skipped": True}

    from aqsp.ledger import validate_predictions
    from aqsp.ledger.base import read_ledger

    rows = read_ledger(str(ledger_path))
    if not rows:
        logger.info("  Ledger 为空, 跳过验证")
        return {"checked": 0, "skipped": True}

    symbols_in_ledger: list[str] = []
    benchmark_symbols: list[str] = []
    seen_symbols: set[str] = set()
    seen_benchmarks: set[str] = set()
    for row in rows:
        sym = str(row.get("symbol", "") or "")
        if sym and sym not in seen_symbols:
            seen_symbols.add(sym)
            symbols_in_ledger.append(sym)
        benchmark = str(row.get("benchmark_symbol", "") or "")
        if benchmark and benchmark not in seen_benchmarks:
            seen_benchmarks.add(benchmark)
            benchmark_symbols.append(benchmark)

    if not symbols_in_ledger and not benchmark_symbols:
        return {"checked": 0}

    frames, attempted_sources = _fetch_history_frames_resilient(
        config,
        symbols_in_ledger,
        days=60,
        logger=logger,
        benchmark_symbols=benchmark_symbols,
    )

    validation = validate_predictions(str(ledger_path), frames)

    result: dict[str, Any] = {
        "checked": validation.checked,
        "wins": validation.wins,
        "avg_return_pct": round(validation.avg_return_pct, 2),
        "avg_excess_pct": round(validation.avg_excess_pct, 2),
        "frames_loaded": len(frames),
        "sources_attempted": attempted_sources,
    }

    if validation.checked > 0:
        win_rate = validation.wins / validation.checked * 100
        logger.info("  验证完成: %d 条, 胜率 %.1f%%", validation.checked, win_rate)
        logger.info(
            "  平均收益: %.2f%%, 平均超额: %.2f%%",
            validation.avg_return_pct,
            validation.avg_excess_pct,
        )
    else:
        logger.info("  暂无可验证的历史预测")

    return result


def _step_sync_paper_trades(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  同步虚拟盘持仓")

    ledger_path = config.project_root / config.ledger_path
    paper_ledger_path = config.project_root / config.paper_ledger
    paper_report_path = config.project_root / config.paper_report_path
    if not ledger_path.exists():
        logger.info("  Ledger 文件不存在, 跳过虚拟盘同步")
        return {"skipped": True}

    from aqsp.ledger.base import read_ledger
    from aqsp.paper import read_paper_trades, render_paper_report, sync_paper_trades

    signal_rows = read_ledger(str(ledger_path))
    if not signal_rows:
        logger.info("  Ledger 为空, 跳过虚拟盘同步")
        return {"skipped": True}

    symbols: list[str] = []
    seen: set[str] = set()
    for row in signal_rows + read_paper_trades(paper_ledger_path):
        symbol = str(row.get("symbol", "")).strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)

    if not symbols:
        logger.info("  无可同步标的, 跳过虚拟盘同步")
        return {"skipped": True}

    frames, attempted_sources = _fetch_history_frames_resilient(
        config,
        symbols,
        days=60,
        logger=logger,
    )
    summary = sync_paper_trades(
        signal_ledger=ledger_path,
        paper_ledger=paper_ledger_path,
        frames=frames,
    )
    paper_rows = read_paper_trades(paper_ledger_path)
    report = render_paper_report(summary=summary, trades=paper_rows)
    paper_report_path.parent.mkdir(parents=True, exist_ok=True)
    paper_report_path.write_text(report, encoding="utf-8")

    logger.info(
        "  虚拟盘同步完成: opened=%d closed=%d open_positions=%d pending=%d",
        summary.opened,
        summary.closed,
        summary.open_positions,
        summary.pending_entry,
    )

    return {
        "opened": summary.opened,
        "closed": summary.closed,
        "open_positions": summary.open_positions,
        "pending_entry": summary.pending_entry,
        "not_executable": summary.not_executable,
        "frames_loaded": len(frames),
        "sources_attempted": attempted_sources,
        "report_size": paper_report_path.stat().st_size
        if paper_report_path.exists()
        else 0,
    }


def _step_adaptive_learning(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  自适应学习: 分析历史表现生成策略权重提案")

    ledger_path = config.project_root / config.ledger_path
    if not ledger_path.exists():
        logger.info("  Ledger 不存在, 跳过学习")
        return {"skipped": True}

    from aqsp.ledger.base import ledger_rows_to_frame, read_ledger
    from aqsp.ledger.learner import (
        PerformanceLearner,
        StrategyDecayDetector,
        format_decay_alerts,
    )

    rows = read_ledger(str(ledger_path))
    if not rows:
        logger.info("  Ledger 为空, 跳过学习")
        return {"skipped": True}

    ledger_df = ledger_rows_to_frame(rows)
    independent_signal_days = 0
    if not ledger_df.empty and "signal_date" in ledger_df.columns:
        signal_dates = pd.to_datetime(
            ledger_df["signal_date"], errors="coerce"
        ).dropna()
        independent_signal_days = signal_dates.dt.date.nunique()

    learner = PerformanceLearner()
    weights = learner.compute_weights(ledger_df)

    result: dict[str, Any] = {
        "weights_proposed": bool(weights),
        "weights_applied": False,
        "independent_signal_days": independent_signal_days,
    }
    if weights:
        logger.info("  学习到的策略权重提案（仅研究观察，未应用到正式筛选）:")
        for k, v in sorted(weights.items()):
            logger.info("    %s: %.3f", k, v)
        result["proposed_weights"] = {k: round(v, 3) for k, v in weights.items()}

    if independent_signal_days < learner.config.min_independent_signal_days:
        logger.info(
            "  冷启动未满: %d/%d 个独立信号日，跳过策略衰减告警",
            independent_signal_days,
            learner.config.min_independent_signal_days,
        )
        result["decay_alerts"] = 0
        result["cold_start_skip"] = True
        return result

    decay_detector = StrategyDecayDetector()
    decay_alerts = decay_detector.detect(ledger_df)
    if decay_alerts:
        logger.warning("  检测到策略衰减:")
        alert_text = format_decay_alerts(decay_alerts)
        for line in alert_text.split("\n"):
            if line.strip():
                logger.warning("    %s", line)
        result["decay_alerts"] = len(decay_alerts)
    else:
        result["decay_alerts"] = 0

    return result


def _step_auto_evolution(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  执行策略自进化")

    if not config.enable_auto_evolution:
        logger.info("  自进化已禁用，跳过")
        return {"skipped": True, "reason": "disabled"}

    output_path = config.project_root / "data" / "evolution_result.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    argv = [
        "evolve",
        "--source",
        config.source,
        "--output",
        str(output_path),
    ]

    from aqsp.cli import main

    exit_code = main(argv)
    if exit_code != 0:
        raise DataError(f"策略自进化失败, exit_code={exit_code}")

    payload: dict[str, Any] = {}
    if output_path.exists():
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}

    strategy_name = str(payload.get("strategy_name", "") or "")
    confidence = float(payload.get("confidence", 0) or 0)
    improvement = float(payload.get("performance_improvement", 0) or 0)
    reason = str(payload.get("reason", "") or "当前无需进化")

    if strategy_name:
        logger.info(
            "  自进化结果: strategy=%s improvement=%.2f%% confidence=%.2f%%",
            strategy_name,
            improvement * 100,
            confidence * 100,
        )
        if _notify_fanout_enabled(config):
            from aqsp.notifier import send_notification

            send_notification(
                "策略自进化",
                "\n".join(
                    [
                        f"- 策略: {strategy_name}",
                        f"- 性能提升: {improvement:.2%}",
                        f"- 置信度: {confidence:.2%}",
                        f"- 结论: {reason}",
                    ]
                ),
            )
    else:
        logger.info("  自进化结果: %s", reason)

    return {
        "exit_code": exit_code,
        "evolved": bool(strategy_name),
        "strategy_name": strategy_name,
        "confidence": confidence,
        "performance_improvement": improvement,
        "reason": reason,
    }


def _step_generate_report(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  生成每日简报")

    argv_briefing = [
        "briefing",
        "--ledger",
        config.ledger_path,
        "--output",
        config.briefing_path,
    ]
    if _notify_fanout_enabled(config):
        argv_briefing.append("--notify")

    from aqsp.cli import main

    exit_code = main(argv_briefing)

    briefing_path = config.project_root / config.briefing_path
    briefing_size = briefing_path.stat().st_size if briefing_path.exists() else 0

    logger.info("  简报已生成: %s (%d bytes)", config.briefing_path, briefing_size)

    return {"exit_code": exit_code, "briefing_size": briefing_size}


def _step_refresh_dashboard(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  刷新 Dashboard")

    try:
        sys.path.insert(0, str(config.project_root / "scripts"))
        from export_dashboard_db import export_db
        from render_dashboard import read_candidates, render_all_panels

        csv_path = config.project_root / config.csv_path
        ledger_path = config.project_root / config.ledger_path
        paper_ledger_path = config.project_root / config.paper_ledger
        output_path = config.project_root / config.dashboard_html
        db_path = config.project_root / config.dashboard_db

        html = render_all_panels(
            candidates=read_candidates(csv_path),
            ledger_path=str(ledger_path),
            paper_ledger_path=str(paper_ledger_path),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        export_db(csv_path, ledger_path, db_path)
        logger.info("  Dashboard 已保存: %s", config.dashboard_html)
        logger.info("  Dashboard 数据库已保存: %s", config.dashboard_db)
        return {
            "exit_code": 0,
            "html_size": len(html),
            "db_size": db_path.stat().st_size if db_path.exists() else 0,
        }
    except Exception as exc:
        logger.warning("  Dashboard 刷新失败(非致命): %s", exc)
        return {"exit_code": 1, "error": str(exc)}


def _step_cleanup(config: PipelineConfig, logger: logging.Logger) -> dict[str, Any]:
    logger.info("  清理过期数据和日志")

    from aqsp.data.cache import DataCache

    cache = DataCache()
    cleared = cache.clear_expired(max_age_hours=168)
    logger.info("  清理缓存: %d 条", cleared)

    log_dir = config.project_root / "logs" / "daily"
    if log_dir.exists():
        import time

        cutoff = time.time() - 30 * 86400
        removed_logs = 0
        for log_file in log_dir.glob("*.log"):
            if log_file.stat().st_mtime < cutoff:
                log_file.unlink()
                removed_logs += 1
        if removed_logs > 0:
            logger.info("  清理旧日志: %d 个文件", removed_logs)
    else:
        removed_logs = 0

    return {"cache_cleared": cleared, "logs_removed": removed_logs}


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    logger = logging.getLogger("aqsp.pipeline")
    started = now_shanghai()
    steps: list[StepResult] = []

    logger.info("=" * 60)
    logger.info("AI量化选股 - 每日跑批开始")
    logger.info("时间: %s", started.isoformat(timespec="seconds"))
    logger.info("模式: %s", "dry-run" if config.dry_run else "正式运行")
    logger.info("=" * 60)

    today = today_shanghai()
    if not _is_trade_day(today):
        logger.info("今日 (%s) 非交易日, 仅执行数据更新和报告生成", today.isoformat())

    pipeline_steps: list[tuple[str, Any]] = [
        ("数据更新", lambda: _step_update_data(config, logger)),
        ("策略运行", lambda: _step_run_strategy(config, logger)),
        ("预测验证", lambda: _step_validate_predictions(config, logger)),
        ("虚拟盘同步", lambda: _step_sync_paper_trades(config, logger)),
        ("收盘复盘", lambda: _step_closing_review(config, logger)),
        ("自适应学习", lambda: _step_adaptive_learning(config, logger)),
        ("策略自进化", lambda: _step_auto_evolution(config, logger)),
        ("报告生成", lambda: _step_generate_report(config, logger)),
        ("Dashboard刷新", lambda: _step_refresh_dashboard(config, logger)),
        ("数据清理", lambda: _step_cleanup(config, logger)),
    ]
    if not _is_trade_day(today):
        pipeline_steps = [
            ("数据更新", lambda: _step_update_data(config, logger)),
            ("报告生成", lambda: _step_generate_report(config, logger)),
            ("Dashboard刷新", lambda: _step_refresh_dashboard(config, logger)),
            ("数据清理", lambda: _step_cleanup(config, logger)),
        ]

    for step_name, step_fn in pipeline_steps:
        result = _run_step(step_name, step_fn, logger, dry_run=config.dry_run)
        steps.append(result)

        if not result.success and step_name in ("数据更新", "策略运行"):
            logger.error("关键步骤 '%s' 失败, 终止后续流程", step_name)
            break

    finished = now_shanghai()
    duration = (finished - started).total_seconds()
    success_count = sum(1 for s in steps if s.success)
    total_count = len(steps)
    overall_success = all(s.success for s in steps)

    summary_lines = [
        f"跑批完成: {success_count}/{total_count} 步骤成功",
        f"总耗时: {duration:.1f}s",
    ]
    for s in steps:
        status = "✓" if s.success else "✗"
        summary_lines.append(
            f"  {status} {s.name}: {s.duration_seconds:.1f}s"
            + (f" - {s.message}" if s.message else "")
        )

    summary = "\n".join(summary_lines)
    logger.info(summary)
    logger.info("=" * 60)

    return PipelineResult(
        started_at=started.isoformat(timespec="seconds"),
        finished_at=finished.isoformat(timespec="seconds"),
        duration_seconds=duration,
        steps=steps,
        overall_success=overall_success,
        summary=summary,
    )


def _notify_fanout_enabled(config: PipelineConfig) -> bool:
    return config.notify and config.notify_mode == "fanout"


def _latest_source_status_from_ledger(ledger_path: Path) -> dict[str, Any] | None:
    if not ledger_path.exists():
        return None
    latest: dict[str, Any] | None = None
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("run_requested_source") or row.get("run_actual_source"):
            latest = row
    if latest is None:
        return None
    return {
        "requested_source": str(latest.get("run_requested_source", "") or ""),
        "actual_source": str(latest.get("run_actual_source", "") or ""),
        "health_label": str(latest.get("run_source_health_label", "") or "unknown"),
        "health_message": str(
            latest.get("run_source_health_message", "") or "暂无说明"
        ),
    }


def _latest_portfolio_summary(config: PipelineConfig) -> Any | None:
    csv_path = config.project_root / config.csv_path
    if not csv_path.exists():
        return None

    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return None
    if df.empty:
        return None

    from aqsp.core.types import PickResult
    from aqsp.portfolio.manager import PortfolioDecision, summarize_portfolio_decisions

    def _text(value: Any) -> str:
        if value is None or pd.isna(value):
            return ""
        return str(value).strip()

    def _symbol(value: Any) -> str:
        text = _text(value)
        if not text:
            return ""
        if "." in text:
            text = text.split(".", 1)[0]
        return text.zfill(6) if text.isdigit() else text

    def _num(value: Any) -> float:
        if value is None or pd.isna(value):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    picks: list[PickResult] = []
    decisions: list[PortfolioDecision] = []
    for row in df.to_dict(orient="records"):
        action = _text(row.get("portfolio_action")) or "keep"
        picks.append(
            PickResult(
                symbol=_symbol(row.get("symbol")),
                name=_text(row.get("name")),
                date=_text(row.get("date")),
                close=_num(row.get("close")),
                score=_num(row.get("score")),
                rating=_text(row.get("rating")),
                entry_type=_text(row.get("entry_type")),
                ideal_buy=_num(row.get("ideal_buy")),
                stop_loss=_num(row.get("stop_loss")),
                take_profit=_num(row.get("take_profit")),
                position=_text(row.get("position")),
                strategies=tuple(),
                reasons=tuple(),
                risks=tuple(),
                metrics={"portfolio_action": action},
            )
        )
        decisions.append(
            PortfolioDecision(
                symbol=_symbol(row.get("symbol")),
                action=action,
                score_delta=0.0,
                reasons=("保持原排序",),
            )
        )
    return summarize_portfolio_decisions(picks, decisions)


def _read_latest_candidates(config: PipelineConfig) -> list[dict[str, Any]]:
    csv_path = config.project_root / config.csv_path
    if not csv_path.exists():
        return []
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return []
    if df.empty:
        return []

    from aqsp.ratings import portfolio_action_label, rating_label

    def _text(value: Any) -> str:
        if value is None or pd.isna(value):
            return ""
        return str(value).strip()

    def _symbol(value: Any) -> str:
        text = _text(value)
        if not text:
            return ""
        if "." in text:
            text = text.split(".", 1)[0]
        return text.zfill(6) if text.isdigit() else text

    def _score(value: Any) -> float:
        if value is None or pd.isna(value):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _split_points(value: Any, limit: int = 2) -> tuple[str, ...]:
        text = _text(value)
        if not text:
            return ()
        parts = [
            part.strip() for part in text.replace(";", "；").split("；") if part.strip()
        ]
        return tuple(parts[:limit])

    def _review_priority_label(value: Any) -> str:
        return review_priority_label(_text(value))

    candidates: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        symbol = _symbol(row.get("symbol"))
        name = _text(row.get("name"))
        display = symbol if not name or name == symbol else f"{symbol} {name}"
        raw_rating = _text(row.get("rating"))
        raw_action = _text(row.get("portfolio_action")) or "keep"
        candidates.append(
            {
                "symbol": symbol,
                "display": display,
                "date": _text(row.get("date")),
                "score": _score(row.get("score")),
                "rating": raw_rating,
                "rating_label": rating_label(raw_rating),
                "action": raw_action,
                "action_label": portfolio_action_label(raw_action),
                "reasons": _split_points(row.get("reasons")),
                "risks": _split_points(row.get("risks")),
                "candidate_status": _text(row.get("candidate_status")),
                "candidate_blocker": _text(row.get("candidate_blocker")),
                "candidate_next_step": _text(row.get("candidate_next_step")),
                "candidate_review_window": _text(row.get("candidate_review_window")),
                "candidate_review_priority": _review_priority_label(
                    row.get("candidate_review_priority")
                ),
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates


def _dedupe_points(points: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for point in points:
        clean = str(point).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
        if len(deduped) >= limit:
            break
    return deduped


def _format_candidate_summary_line(candidate: dict[str, Any]) -> str:
    parts = [
        candidate["display"],
        candidate["rating_label"],
    ]
    status = str(candidate.get("candidate_status", "") or "")
    if status:
        parts.append(status)
    parts.append(f"PM {candidate['action_label']}")
    parts.append(f"评分 {candidate['score']:.1f}")
    return "- " + " | ".join(parts)


def _format_candidate_review_meta(candidate: dict[str, Any]) -> str:
    return format_review_meta(
        str(candidate.get("candidate_review_priority", "") or ""),
        str(candidate.get("candidate_review_window", "") or ""),
    )


def _build_pipeline_digest(
    config: PipelineConfig,
    result: PipelineResult,
) -> str:
    step_total = len(result.steps)
    step_success = sum(1 for step in result.steps if step.success)
    failed_steps = [step for step in result.steps if not step.success]

    portfolio_summary = _latest_portfolio_summary(config)
    candidates = _read_latest_candidates(config)
    latest_signal_day = next(
        (str(item.get("date", "")).strip() for item in candidates if item.get("date")),
        "",
    )

    summary_target = ""
    if portfolio_summary is not None and portfolio_summary.top_focus:
        summary_target = "、".join(portfolio_summary.top_focus[:2])
    elif portfolio_summary is not None and portfolio_summary.watchlist:
        summary_target = "、".join(
            str(item).split("(", 1)[0] for item in portfolio_summary.watchlist[:2]
        )

    if failed_steps:
        conclusion = "流程未全绿，先排障，再看本次信号。"
    elif portfolio_summary is not None and portfolio_summary.top_focus:
        conclusion = f"今日主链聚焦 {summary_target}，其余候选继续分层跟踪。"
    elif portfolio_summary is not None and portfolio_summary.watchlist:
        conclusion = f"今日暂无重点名单，先围绕 {summary_target} 做观察跟踪。"
    else:
        conclusion = "今日未形成明确主链，结果以观察为主。"

    review_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("candidate_next_step") or candidate.get("candidate_blocker")
    ]

    core_lines = [
        f"**🎯 今日结论**：{conclusion}",
        f"**📦 PM 主裁决**：{portfolio_summary.headline if portfolio_summary is not None else '暂无可用候选输出'}",
    ]
    if latest_signal_day:
        core_lines.append(f"**📅 信号日期**：{latest_signal_day}")
    if portfolio_summary is not None and portfolio_summary.top_focus:
        core_lines.append(
            "**⭐ 今日重点名单**：" + "、".join(portfolio_summary.top_focus)
        )
    elif portfolio_summary is not None and portfolio_summary.watchlist:
        core_lines.append(
            "**👀 观察主线**："
            + "、".join(
                str(item).split("(", 1)[0] for item in portfolio_summary.watchlist[:3]
            )
        )
    if portfolio_summary is not None and portfolio_summary.execution_blockers:
        core_lines.append(
            "**🔒 现在卡在哪**："
            + "；".join(
                normalize_research_tone(str(item))
                for item in portfolio_summary.execution_blockers[:2]
            )
        )
    elif review_candidates:
        blockers = [
            str(candidate.get("candidate_blocker", "") or "")
            for candidate in review_candidates
            if candidate.get("candidate_blocker")
        ]
        if blockers:
            core_lines.append(
                "**🔒 现在卡在哪**："
                + "；".join(normalize_research_tone(item) for item in blockers[:2])
            )
    if review_candidates:
        lead_review = review_candidates[0]
        lead_meta = _format_candidate_review_meta(lead_review)
        lead_line = f"**📝 首要复核**：{lead_review['display']}"
        if lead_meta:
            lead_line += f" | {lead_meta}"
        core_lines.append(lead_line)
    core_lines.append(
        f"**✅ 流程状态**：{step_success}/{step_total} 成功 | {result.duration_seconds:.1f}s"
    )
    if failed_steps:
        core_lines.append(
            "**🚨 异常步骤**：" + "、".join(step.name for step in failed_steps[:3])
        )

    main_chain_lines: list[str] = []
    if candidates:
        for candidate in candidates[:3]:
            main_chain_lines.append(_format_candidate_summary_line(candidate))
            if candidate["reasons"]:
                main_chain_lines.append(
                    "  关注点: " + "；".join(candidate["reasons"][:2])
                )
            if candidate.get("candidate_blocker"):
                main_chain_lines.append(
                    "  现在卡在哪: " + str(candidate["candidate_blocker"])
                )
            if candidate.get("candidate_next_step"):
                main_chain_lines.append(
                    "  下一步: "
                    + normalize_research_tone(str(candidate["candidate_next_step"]))
                )
            review_meta = _format_candidate_review_meta(candidate)
            if review_meta:
                main_chain_lines.append("  再看时间: " + review_meta)
        if portfolio_summary is not None and portfolio_summary.watchlist:
            main_chain_lines.append(
                "- 继续观察名单: "
                + "、".join(
                    str(item).split("(", 1)[0]
                    for item in portfolio_summary.watchlist[:5]
                )
            )
        if portfolio_summary is not None and portfolio_summary.top_focus:
            main_chain_lines.append(
                "- 再看顺序: 先看 " + " → ".join(portfolio_summary.top_focus[:2])
            )
        elif review_candidates:
            main_chain_lines.append("- 观察名单接下来:")
            for candidate in review_candidates[:2]:
                main_chain_lines.append(
                    "  - "
                    + format_watch_review_line(
                        candidate["display"],
                        priority=str(
                            candidate.get("candidate_review_priority", "") or ""
                        ),
                        review_window=str(
                            candidate.get("candidate_review_window", "") or ""
                        ),
                        next_step=str(candidate.get("candidate_next_step", "") or ""),
                    )
                )
    elif portfolio_summary is not None:
        if portfolio_summary.top_focus:
            main_chain_lines.append(
                "- 今日重点名单: " + "、".join(portfolio_summary.top_focus)
            )
        elif portfolio_summary.watchlist:
            main_chain_lines.append(
                "- 继续观察名单: " + "、".join(portfolio_summary.watchlist)
            )
    else:
        main_chain_lines.append("- 暂无可用候选输出")

    plan_lines = []
    if failed_steps:
        plan_lines.append(
            "- 明日先修复失败步骤对应的数据源或运行配置，再复核本次输出。"
        )
    elif portfolio_summary is not None and portfolio_summary.top_focus:
        action_line = "- 明日先核对今日重点名单是否延续量价确认，再决定人工跟踪优先级。"
        if portfolio_summary.top_focus:
            action_line = (
                "- 明日先盯 "
                + " → ".join(portfolio_summary.top_focus[:2])
                + " 的开盘强弱与流动性，再决定人工跟踪优先级。"
            )
        plan_lines.append(action_line)
        lead_review = review_candidates[0] if review_candidates else None
        if lead_review is not None:
            plan_lines.append(
                "- 观察名单接下来: "
                + format_watch_review_action(
                    lead_review["display"],
                    priority=str(
                        lead_review.get("candidate_review_priority", "") or ""
                    ),
                    review_window=str(
                        lead_review.get("candidate_review_window", "") or ""
                    ),
                    next_step=str(lead_review.get("candidate_next_step", "") or ""),
                )
            )
    elif review_candidates:
        lead_review = review_candidates[0]
        plan_lines.append(
            "- 明日"
            + format_watch_review_action(
                lead_review["display"],
                priority=str(lead_review.get("candidate_review_priority", "") or ""),
                review_window=str(lead_review.get("candidate_review_window", "") or ""),
                next_step=str(lead_review.get("candidate_next_step", "") or ""),
            )
        )
    elif portfolio_summary is not None and portfolio_summary.watchlist:
        plan_lines.append(
            "- 明日以继续观察名单再看为主，等待右侧确认，不放大纸面仓位。"
        )
    else:
        plan_lines.append("- 明日无明确主链复核，优先确认数据源和策略运行是否完整。")
    if portfolio_summary is not None and portfolio_summary.allocation_note:
        plan_lines.append(
            f"- 纸面约束: {normalize_research_tone(portfolio_summary.allocation_note)}。"
        )
    plan_lines.extend(
        [
            "- 对照收盘复盘，确认强弱分层、策略标签和 PM 裁决是否一致。",
            "- 若继续观察名单仍然拥挤，只保留最强一到两只做人工跟踪。",
        ]
    )

    risk_points: list[str] = []
    if portfolio_summary is not None and not portfolio_summary.top_focus:
        risk_points.append("当前没有进入纸面复核区的主链候选，纸面仓位不宜放大。")
    if portfolio_summary is not None and portfolio_summary.downgrade_count > 0:
        risk_points.append(
            f"PM 已将 {portfolio_summary.downgrade_count} 只候选降级，说明拥挤度或确定性仍不足。"
        )
    if portfolio_summary is not None:
        for blocker in portfolio_summary.execution_blockers[:2]:
            risk_points.append(str(blocker))
        for hotspot in portfolio_summary.action_hotspots[:2]:
            risk_points.append(str(hotspot))
    for candidate in candidates[:3]:
        for risk in candidate["risks"][:1]:
            risk_points.append(f"{candidate['display']}: {risk}")
    if not result.overall_success:
        risk_points.append("总流程未全绿，先排查失败步骤再继续自动化。")
    risk_lines = [f"- {point}" for point in _dedupe_points(risk_points, limit=4)]
    if not risk_lines:
        risk_lines.append("- 当前流程正常，保持纸面复核节奏。")

    lines = [
        "## 结论",
        *core_lines,
        "",
        "## 候选",
        *main_chain_lines,
        "",
        "## 风险",
        *risk_lines,
        "",
        "## 明日",
        *plan_lines,
    ]
    if result.steps and failed_steps:
        lines.extend(["", "## 运行"])
        for step in result.steps:
            badge = "OK" if step.success else "FAIL"
            line = f"- {badge} {step.name}: {step.duration_seconds:.1f}s"
            if step.message:
                line += f" ({step.message})"
            lines.append(line)
    from aqsp.notification_style import compact_notification_markdown

    return compact_notification_markdown("\n".join(lines))


def _send_pipeline_digest(
    config: PipelineConfig,
    result: PipelineResult,
    logger: logging.Logger,
) -> None:
    if not config.notify or config.notify_mode != "summary" or config.dry_run:
        return

    try:
        from aqsp.notifier import prepend_source_status_banner, send_notification

        digest = _build_pipeline_digest(config, result)
        source_status = _latest_source_status_from_ledger(
            config.project_root / config.ledger_path
        )
        if source_status:
            digest = prepend_source_status_banner(digest, source_status)
        run_date = (
            result.started_at[:10]
            if result.started_at
            else today_shanghai().isoformat()
        )
        send_notification(f"收盘总览-{run_date}", digest)
        logger.info("已发送收盘汇总通知 (mode=summary)")
    except Exception as exc:
        logger.warning("收盘汇总通知发送失败(非致命): %s", exc)


def _build_config(args: argparse.Namespace) -> PipelineConfig:
    import os

    from aqsp.config import load_runtime_config

    env = load_runtime_config()

    project_root = (
        Path(args.project_root)
        if args.project_root
        else Path(__file__).resolve().parents[1]
    )

    return PipelineConfig(
        project_root=project_root,
        source=args.source or os.getenv("AQSP_SOURCE", "auto").strip() or "auto",
        mode=args.mode or env.mode,
        limit=args.limit or env.limit,
        max_universe=args.max_universe or env.max_universe,
        min_avg_amount=args.min_avg_amount or env.min_avg_amount,
        max_data_lag_days=args.max_data_lag_days or env.max_data_lag_days,
        enable_online_factors=args.enable_online_factors or env.enable_online_factors,
        allow_online_fallback=env.allow_online_fallback,
        ledger_path=args.ledger or "data/predictions.jsonl",
        report_path=args.report or "reports/latest.md",
        csv_path=args.csv or "reports/latest.csv",
        briefing_path=args.briefing or "reports/briefing.md",
        paper_report_path="reports/paper.md",
        dashboard_html=args.dashboard_html or "dist/dashboard/index.html",
        dashboard_db=args.dashboard_db or "dist/dashboard/aqsp.db",
        paper_ledger=args.paper_ledger or "data/paper_trades.jsonl",
        closing_review_path="reports/closing_review.md",
        notify=args.notify or env.notify,
        notify_mode=env.notify_mode,
        dry_run=args.dry_run,
        enable_debate=args.enable_debate or env.enable_debate,
        enable_auto_evolution=env.enable_auto_evolution,
    )


def _write_result_file(result: PipelineResult, project_root: Path) -> None:
    result_dir = project_root / "logs" / "pipeline"
    result_dir.mkdir(parents=True, exist_ok=True)

    today_str = today_shanghai().isoformat()
    result_file = result_dir / f"{today_str}.json"

    payload = {
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_seconds": round(result.duration_seconds, 1),
        "overall_success": result.overall_success,
        "steps": [
            {
                "name": s.name,
                "success": s.success,
                "duration_seconds": round(s.duration_seconds, 1),
                "message": s.message,
                "details": s.details,
            }
            for s in result.steps
        ],
    }

    result_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _append_daily_run_history(result, project_root)


def _append_daily_run_history(result: PipelineResult, project_root: Path) -> None:
    history_path = project_root / "data" / "daily_run_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "date": result.finished_at[:10],
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "success": result.overall_success,
        "exit_code": 0 if result.overall_success else 1,
        "successful_steps": sum(1 for step in result.steps if step.success),
        "total_steps": len(result.steps),
    }
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="daily_pipeline",
        description="AI量化选股 - 每日跑批主脚本",
    )

    parser.add_argument("--dry-run", action="store_true", help="试运行, 不实际执行策略")
    parser.add_argument("--verbose", action="store_true", help="详细日志输出")
    parser.add_argument("--notify", action="store_true", help="发送通知")
    parser.add_argument(
        "--enable-debate", action="store_true", help="启用多Agent辩论分析"
    )
    parser.add_argument(
        "--enable-online-factors", action="store_true", help="启用在线因子"
    )
    parser.add_argument("--source", default="", help="数据源")
    parser.add_argument("--mode", default="", help="选股模式 (open/close)")
    parser.add_argument("--limit", type=int, default=0, help="候选数量")
    parser.add_argument("--max-universe", type=int, default=0, help="最大标的池")
    parser.add_argument(
        "--min-avg-amount", type=float, default=0, help="最低日均成交额"
    )
    parser.add_argument(
        "--max-data-lag-days", type=int, default=0, help="最大数据延迟天数"
    )
    parser.add_argument("--ledger", default="", help="Ledger 文件路径")
    parser.add_argument("--report", default="", help="报告输出路径")
    parser.add_argument("--csv", default="", help="CSV 输出路径")
    parser.add_argument("--briefing", default="", help="简报输出路径")
    parser.add_argument("--dashboard-html", default="", help="Dashboard HTML 路径")
    parser.add_argument("--dashboard-db", default="", help="Dashboard 数据库路径")
    parser.add_argument("--paper-ledger", default="", help="模拟交易 Ledger 路径")
    parser.add_argument("--project-root", default="", help="项目根目录")

    args = parser.parse_args(argv)

    config = _build_config(args)

    log_dir = config.project_root / "logs" / "daily"
    logger = _setup_logging(args.verbose, log_dir)

    try:
        result = run_pipeline(config)
        _write_result_file(result, config.project_root)
        _send_pipeline_digest(config, result, logger)

        if result.overall_success:
            logger.info("跑批成功完成")
            return 0
        else:
            logger.warning("跑批完成但有步骤失败")
            return 1

    except KeyboardInterrupt:
        logger.warning("用户中断")
        return 130
    except Exception as exc:
        logger.critical("跑批异常终止: %s", exc)
        logger.debug(traceback.format_exc())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
