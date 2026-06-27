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
    format_watch_review_line,
    normalize_research_tone,
    review_priority_label,
)
from aqsp.core.errors import DataError, FreshnessError
from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.utils.jsonl_io import append_jsonl, atomic_write_text


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
    notify_status: dict[str, Any] = field(default_factory=dict)


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


def _latest_validation_details(result: PipelineResult) -> dict[str, Any]:
    for step in reversed(result.steps):
        if step.name == "预测验证" and step.success and step.details:
            return step.details
    return {}


def _format_validation_digest_lines(result: PipelineResult) -> list[str]:
    details = _latest_validation_details(result)
    if not details:
        return []

    checked = int(details.get("checked") or 0)
    skipped = int(details.get("skipped_not_executable") or 0)
    if checked <= 0 and skipped <= 0:
        return ["- 策略自检: 暂无可验证历史预测"]

    pieces: list[str] = []
    if checked > 0:
        wins = int(details.get("wins") or 0)
        pieces.append(f"验证 {checked} 条")
        pieces.append(f"胜率 {wins / checked * 100:.1f}%")
        pieces.append(f"平均收益 {float(details.get('avg_return_pct') or 0.0):.2f}%")
    if skipped > 0:
        pieces.append(f"不可成交跳过 {skipped} 条")

    lines = ["- 策略自检: " + " / ".join(pieces)]
    reasons = details.get("not_executable_reasons") or {}
    if skipped > 0 and isinstance(reasons, dict) and reasons:
        top_reasons = sorted(
            ((str(reason), int(count)) for reason, count in reasons.items()),
            key=lambda item: (-item[1], item[0]),
        )[:3]
        lines.append(
            "- 不可成交原因: "
            + ", ".join(f"{reason}×{count}" for reason, count in top_reasons)
        )
    rates = details.get("strategy_not_executable_rates") or {}
    if skipped > 0 and isinstance(rates, dict) and rates:
        top_rates = sorted(
            ((str(strategy), float(rate)) for strategy, rate in rates.items()),
            key=lambda item: (-item[1], item[0]),
        )[:3]
        lines.append(
            "- 不可成交策略: "
            + ", ".join(f"{strategy} {rate:.0%}" for strategy, rate in top_rates)
        )
    return lines


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
    from aqsp.data.source_factory import build_data_source

    return build_data_source(config.source)


def _build_resilient_history_source(config: PipelineConfig) -> Any:
    from aqsp.data.source_factory import build_data_source

    if config.source in {"auto", "local_first"}:
        return build_data_source("local_first")
    return build_data_source("online_first")


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
        source = _build_data_source(config)
        if (
            config.source == "sqlite_db"
            and hasattr(source, "get_symbols_with_daily_coverage")
            and hasattr(source, "get_available_symbols")
        ):
            available = source.get_available_symbols()
            if available:
                end = today_shanghai()
                start = end - timedelta(days=365 * 8)
                covered = source.get_symbols_with_daily_coverage(
                    available,
                    start,
                    end,
                )
                if covered:
                    selected = (
                        covered[: config.max_universe]
                        if config.max_universe > 0
                        else covered
                    )
                    logger.info("  sqlite_db 覆盖过滤后保留 %d 只标的", len(selected))
                    return list(selected)
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
                return (
                    available[: config.max_universe]
                    if config.max_universe > 0
                    else available
                )
    except Exception as exc:
        logger.warning("  自动选取标的失败, 使用默认池: %s", exc)

    return list(DEFAULT_SYMBOLS)


def _step_run_strategy(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  执行选股策略 (mode=%s, limit=%d)", config.mode, config.limit)
    symbols = _resolve_symbols(config, logger)

    argv = [
        "run",
        "--source",
        config.source,
        "--symbols",
        ",".join(symbols),
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
        "000300",
        "--ledger",
        config.ledger_path,
        "--report",
        config.report_path,
        "--output-csv",
        config.csv_path,
    ]
    if config.enable_debate:
        argv.append("--enable-debate")
    if config.enable_online_factors:
        argv.append("--enable-online-factors")

    from aqsp.cli import main

    exit_code = main(argv)

    if exit_code != 0:
        if exit_code == 2:
            logger.warning("  策略运行完成但熔断器触发 (exit_code=2)")
        else:
            raise DataError(f"策略运行失败, exit_code={exit_code}")

    report_path = Path(config.project_root / config.report_path)
    report_size = report_path.stat().st_size if report_path.exists() else 0
    report_text = (
        report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    )
    gate_blocked = (
        "未通过 walk-forward 双门验证" in report_text
        or report_text.startswith("> ⚠️ **未通过 walk-forward 双门验证")
    )
    logger.info("  报告已生成: %s (%d bytes)", config.report_path, report_size)

    result: dict[str, Any] = {
        "exit_code": 0,
        "raw_exit_code": exit_code,
        "report_size": report_size,
        "gate_ok": not gate_blocked,
    }
    if exit_code == 2:
        result["circuit_breaker"] = True
        result["circuit_breaker_message"] = "组合保护已触发"
    return result


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
        "--max-universe",
        str(config.max_universe),
        "--benchmark-symbol",
        "000300",
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
        "--max-universe",
        str(config.max_universe),
        "--benchmark-symbol",
        "000300",
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
        atomic_write_text(dated_output, latest_output.read_text(encoding="utf-8"))

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
        "skipped_not_executable": int(
            getattr(validation, "skipped_not_executable", 0) or 0
        ),
        "not_executable_reasons": getattr(validation, "not_executable_reasons", None)
        or {},
        "strategy_not_executable_rates": getattr(
            validation, "strategy_not_executable_rates", None
        )
        or {},
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
    atomic_write_text(paper_report_path, report)

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
    from aqsp.ledger.runtime import count_independent_signal_days

    rows = read_ledger(str(ledger_path))
    if not rows:
        logger.info("  Ledger 为空, 跳过学习")
        return {"skipped": True}

    ledger_df = ledger_rows_to_frame(rows)
    independent_signal_days = count_independent_signal_days(str(ledger_path))

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

    import os

    explicit_symbols = os.getenv("AQSP_SYMBOLS", "").strip()
    tushare_token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not explicit_symbols and not tushare_token:
        logger.info("  缺少 TUSHARE_TOKEN 且未显式配置 AQSP_SYMBOLS，跳过策略自进化")
        return {"skipped": True, "reason": "missing_tushare_or_symbols"}

    output_path = config.project_root / "data" / "evolution_result.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    argv = [
        "evolve",
        "--source",
        config.source,
        "--output",
        str(output_path),
    ]

    import contextlib
    import io

    from aqsp.cli import main

    output_buffer = io.StringIO()
    with (
        contextlib.redirect_stdout(output_buffer),
        contextlib.redirect_stderr(output_buffer),
    ):
        exit_code = main(argv)
    cli_output = output_buffer.getvalue().strip()
    for line in cli_output.splitlines():
        logger.info("  %s", line)
    if exit_code != 0:
        if "requires TUSHARE_TOKEN or explicit --symbols" in cli_output:
            logger.info("  缺少可用成分股数据，跳过策略自进化")
            return {"skipped": True, "reason": "missing_pool_constituents"}
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
            from aqsp.notification_runtime import (
                dispatch_notification_once,
                notification_state_path,
            )

            run_date = today_shanghai().isoformat()
            dispatch_notification_once(
                "\n".join(
                    [
                        f"# 策略自进化-{run_date}",
                        "",
                        "## 结论",
                        "",
                        f"- 策略: {strategy_name}",
                        f"- 性能提升: {improvement:.2%}",
                        f"- 置信度: {confidence:.2%}",
                        f"- 结果: {reason}",
                    ]
                ),
                mode=config.notify_mode,
                prefix="auto evolution notify",
                kind=f"auto-evolution:{run_date}",
                state_path=notification_state_path(
                    config.project_root / "data" / "notify_state.json"
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
    config: PipelineConfig, logger: logging.Logger, *, allow_notify: bool = True
) -> dict[str, Any]:
    logger.info("  生成每日简报")

    argv_briefing = [
        "briefing",
        "--ledger",
        config.ledger_path,
        "--output",
        config.briefing_path,
    ]
    if allow_notify and _notify_fanout_enabled(config):
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
        atomic_write_text(output_path, html)
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
        logger.info(
            "今日 (%s) 非交易日, 跳过行情更新和信号写入，仅刷新展示产物",
            today.isoformat(),
        )

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
            (
                "报告生成",
                lambda: _step_generate_report(config, logger, allow_notify=False),
            ),
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
    except Exception as exc:
        logging.getLogger("aqsp.pipeline").warning(
            "读取候选 CSV 失败，无法生成组合摘要: %s",
            exc,
        )
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
    except Exception as exc:
        logging.getLogger("aqsp.pipeline").warning(
            "读取候选 CSV 失败，无法生成候选摘要: %s",
            exc,
        )
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
    *,
    gate_block_reason: str = "",
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

    if gate_block_reason:
        conclusion = "正常候选未放行"
    elif failed_steps:
        conclusion = "主流程未全绿"
    elif portfolio_summary is not None and portfolio_summary.top_focus:
        conclusion = summary_target
    elif portfolio_summary is not None and portfolio_summary.watchlist:
        conclusion = f"观察 {summary_target}"
    else:
        conclusion = "暂无重点名单"

    review_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("candidate_next_step") or candidate.get("candidate_blocker")
    ]

    core_lines = [f"- 结论: {conclusion}"]
    if portfolio_summary is not None and portfolio_summary.headline:
        core_lines.append(f"- PM: {portfolio_summary.headline}")
    if gate_block_reason:
        core_lines.append(f"- 阻塞: {gate_block_reason}")
    if latest_signal_day:
        core_lines.append(f"- 信号日期: {latest_signal_day}")
    if portfolio_summary is not None and portfolio_summary.top_focus:
        core_lines.append("- 重点名单: " + "、".join(portfolio_summary.top_focus))
    elif portfolio_summary is not None and portfolio_summary.watchlist:
        core_lines.append(
            "- 观察名单: "
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
                "- 现在卡在哪: "
                + "；".join(normalize_research_tone(item) for item in blockers[:2])
            )
    if review_candidates:
        lead_review = review_candidates[0]
        lead_meta = _format_candidate_review_meta(lead_review)
        lead_line = f"- 首要复核: {lead_review['display']}"
        if lead_meta:
            lead_line += f" | {lead_meta}"
        core_lines.append(lead_line)
    core_lines.append(f"- 流程: {step_success}/{step_total} 成功 | {result.duration_seconds:.1f}s")
    if failed_steps:
        core_lines.append("- 异常步骤: " + "、".join(step.name for step in failed_steps[:3]))

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
    if gate_block_reason:
        risk_points.append(f"正常候选通知已被阻塞: {gate_block_reason}")
    if not result.overall_success:
        risk_points.append("总流程未全绿，先排查失败步骤再继续自动化。")
    risk_lines = [f"- {point}" for point in _dedupe_points(risk_points, limit=4)]
    validation_lines = _format_validation_digest_lines(result)
    if validation_lines:
        risk_lines.extend(validation_lines)
    if not risk_lines:
        risk_lines.append("- 当前流程正常，保持纸面复核节奏。")

    lines = [
        "## 结果",
        *core_lines,
        "",
        "## 候选",
        *main_chain_lines,
        "",
        "## 风险",
        *risk_lines,
    ]
    from aqsp.notification_style import compact_notification_markdown

    return compact_notification_markdown("\n".join(lines))


def _send_pipeline_digest(
    config: PipelineConfig,
    result: PipelineResult,
    logger: logging.Logger,
) -> None:
    def set_status(status: str, reason: str, **extra: Any) -> None:
        result.notify_status = {
            "mode": config.notify_mode,
            "status": status,
            "reason": reason,
            **extra,
        }

    if not config.notify:
        set_status("skipped", "notify_disabled")
        return
    if config.notify_mode != "summary":
        set_status("skipped", f"notify_mode={config.notify_mode}")
        return
    if config.dry_run:
        set_status("skipped", "dry_run")
        return
    run_date = (
        result.started_at[:10] if result.started_at else today_shanghai().isoformat()
    )
    try:
        digest_date = date.fromisoformat(run_date)
    except ValueError:
        digest_date = today_shanghai()
    if not _is_trade_day(digest_date):
        logger.info("收盘汇总通知跳过：%s 非交易日", run_date)
        set_status("skipped", "non_trading_day", date=run_date)
        return
    gate_block_reason = ""
    if not _pipeline_strategy_gate_ok(result):
        logger.info("收盘汇总通知降级：策略运行未明确通过双门 gate")
        gate_block_reason = "strategy_gate_not_confirmed"
    if gate_block_reason and _gate_block_notification_already_recorded(
        config.project_root,
        run_date,
    ):
        logger.info("收盘汇总通知跳过：当日 gate-block 通知已由主链发送")
        set_status("skipped", "gate_block_already_notified", date=run_date)
        return

    try:
        from aqsp.notifier import prepend_source_status_banner, send_notification

        digest = _build_pipeline_digest(
            config,
            result,
            gate_block_reason=gate_block_reason,
        )
        source_status = _latest_source_status_from_ledger(
            config.project_root / config.ledger_path
        )
        if source_status:
            digest = prepend_source_status_banner(digest, source_status)
        from aqsp.notification_runtime import (
            mark_notification_sent,
            mark_notification_failed,
            notification_state_path,
            reserve_notification,
            should_send_notification,
        )

        state_path = notification_state_path(
            config.project_root / "data" / "notify_state.json"
        )
        notification_state = (
            "gate_block"
            if gate_block_reason
            else "ok"
            if result.overall_success
            else "failure"
        )
        notification_key = f"pipeline-summary:{run_date}:{notification_state}"
        state_markdown = f"# 收盘总览-{run_date}\n\n{digest}"
        if not should_send_notification(
            kind=notification_key,
            markdown=state_markdown,
            state_path=state_path,
        ):
            logger.info("收盘汇总通知已发送过，跳过重复发送 (date=%s)", run_date)
            set_status("skipped", "duplicate_sent", date=run_date)
            return
        if not reserve_notification(
            kind=notification_key,
            markdown=state_markdown,
            state_path=state_path,
        ):
            logger.info("收盘汇总通知已预占位，跳过重复发送 (date=%s)", run_date)
            set_status("skipped", "duplicate_reserved", date=run_date)
            return
        notify_results = send_notification(f"收盘总览-{run_date}", digest)
        if notify_results:
            channel_summary = ", ".join(
                f"{result.channel}={'ok' if result.ok else 'failed'}({result.detail})"
                for result in notify_results
            )
            if any(result.ok for result in notify_results):
                mark_notification_sent(
                    kind=notification_key,
                    markdown=state_markdown,
                    state_path=state_path,
                )
                set_status(
                    "sent",
                    "gate_block_summary_sent" if gate_block_reason else "ok",
                    date=run_date,
                    channels=channel_summary,
                )
            else:
                mark_notification_failed(
                    kind=notification_key,
                    markdown=state_markdown,
                    state_path=state_path,
                )
                set_status(
                    "failed",
                    "all_channels_failed",
                    date=run_date,
                    channels=channel_summary,
                )
            logger.info(
                "已发送收盘汇总通知 (mode=summary, channels=%s)",
                channel_summary,
            )
        else:
            logger.warning("收盘汇总通知未发送：未配置任何通知通道")
            set_status("skipped", "no_channels_configured", date=run_date)
    except Exception as exc:
        logger.warning("收盘汇总通知发送失败(非致命): %s", exc)
        set_status("failed", str(exc), date=run_date)


def _gate_block_notification_already_recorded(
    project_root: Path, run_date: str
) -> bool:
    path = project_root / "data" / "gate_notify_state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    sent_by_date = payload.get("sent_by_date", {})
    if not isinstance(sent_by_date, dict):
        return False
    entry = sent_by_date.get(str(run_date))
    if isinstance(entry, str):
        return bool(entry)
    if not isinstance(entry, dict):
        return False
    return str(entry.get("status") or "") in {"pending", "sent", "failed"}


def _pipeline_strategy_gate_ok(result: PipelineResult) -> bool:
    for step in result.steps:
        if step.name != "策略运行":
            continue
        if not step.success:
            return False
        return step.details.get("gate_ok") is True
    return False


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

    result_date = result.finished_at[:10] or today_shanghai().isoformat()
    result_file = result_dir / f"{result_date}.json"

    payload = {
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_seconds": round(result.duration_seconds, 1),
        "overall_success": result.overall_success,
        "notify_status": result.notify_status,
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

    atomic_write_text(result_file, json.dumps(payload, ensure_ascii=False, indent=2))
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
    append_jsonl(history_path, row)


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
        _send_pipeline_digest(config, result, logger)
        _write_result_file(result, config.project_root)

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
