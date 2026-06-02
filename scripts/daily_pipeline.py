from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

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
    dashboard_html: str
    dashboard_db: str
    paper_ledger: str
    notify: bool
    dry_run: bool
    enable_debate: bool


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
        logger.info("✓ 完成: %s (%.1fs)", name, elapsed)
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
    from aqsp.data.cache import DataCache
    from aqsp.data.multi_source import MultiSource, SourceFactory
    from aqsp.data.eastmoney_source import EastmoneySource
    from aqsp.data.sina_source import SinaSource
    from aqsp.data.tencent_source import TencentSource
    from aqsp.data.akshare_source import AkshareSource
    from aqsp.data.tdx_vipdoc_source import TdxVipdocSource

    logger.info("  拉取最新行情数据 (source=%s)", config.source)

    if config.source in ("auto", "local_first", "multi"):
        if config.allow_online_fallback:
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

    symbols = _resolve_symbols(config, logger)
    frames = fetch_with_source(source, symbols, days=260)

    fresh_count = sum(1 for df in frames.values() if df is not None and not df.empty)
    logger.info("  获取到 %d 只标的数据", fresh_count)

    cache = DataCache()
    cleared = cache.clear_expired(max_age_hours=168)
    if cleared > 0:
        logger.info("  清理过期缓存: %d 条", cleared)

    return {"symbol_count": fresh_count, "cache_cleared": cleared}


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
    if config.notify:
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

    argv = [
        "morning-breakout",
        "--source",
        config.source,
        "--pool",
        "sh300",
        "--top",
        "5",
    ]
    if config.notify:
        argv.append("--notify")

    from aqsp.cli import main

    exit_code = main(argv)

    return {"exit_code": exit_code}


def _step_closing_premium(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  执行尾盘溢价策略")

    argv = [
        "closing-premium",
        "--source",
        config.source,
        "--pool",
        "sh300",
        "--top",
        "5",
    ]
    if config.notify:
        argv.append("--notify")

    from aqsp.cli import main

    exit_code = main(argv)

    return {"exit_code": exit_code}


def _step_closing_review(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  执行收盘复盘")

    argv = [
        "closing-review",
    ]
    if config.notify:
        argv.append("--notify")

    from aqsp.cli import main

    exit_code = main(argv)

    return {"exit_code": exit_code}


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

    from aqsp.data import fetch_with_source
    from aqsp.data.cache import DataCache
    from aqsp.data.multi_source import MultiSource, SourceFactory
    from aqsp.data.eastmoney_source import EastmoneySource
    from aqsp.data.sina_source import SinaSource
    from aqsp.data.tencent_source import TencentSource
    from aqsp.data.akshare_source import AkshareSource
    from aqsp.data.tdx_vipdoc_source import TdxVipdocSource

    symbols_in_ledger: list[str] = []
    seen: set[str] = set()
    for row in rows:
        sym = str(row.get("symbol", ""))
        if sym and sym not in seen:
            seen.add(sym)
            symbols_in_ledger.append(sym)

    if not symbols_in_ledger:
        return {"checked": 0}

    try:
        if config.allow_online_fallback:
            cache = DataCache()
            source = MultiSource(
                SourceFactory("tdx_vipdoc", TdxVipdocSource),
                [
                    EastmoneySource(cache=cache),
                    SinaSource(cache=cache),
                    TencentSource(cache=cache),
                    AkshareSource(cache=cache),
                ],
                validate_consistency=False,
            )
        else:
            source = TdxVipdocSource()
        frames = fetch_with_source(source, symbols_in_ledger, days=60)
    except Exception as exc:
        logger.warning("  验证数据获取失败: %s", exc)
        frames = {}

    validation = validate_predictions(str(ledger_path), frames)

    result: dict[str, Any] = {
        "checked": validation.checked,
        "wins": validation.wins,
        "avg_return_pct": round(validation.avg_return_pct, 2),
        "avg_excess_pct": round(validation.avg_excess_pct, 2),
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


def _step_adaptive_learning(
    config: PipelineConfig, logger: logging.Logger
) -> dict[str, Any]:
    logger.info("  自适应学习: 分析历史表现调整策略权重")

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
    learner = PerformanceLearner()
    weights = learner.compute_weights(ledger_df)

    result: dict[str, Any] = {"weights_updated": bool(weights)}
    if weights:
        logger.info("  学习到的策略权重调整:")
        for k, v in sorted(weights.items()):
            logger.info("    %s: %.3f", k, v)
        result["weights"] = {k: round(v, 3) for k, v in weights.items()}

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
    if config.notify:
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
        ("早盘打板", lambda: _step_morning_breakout(config, logger)),
        ("尾盘溢价", lambda: _step_closing_premium(config, logger)),
        ("收盘复盘", lambda: _step_closing_review(config, logger)),
        ("预测验证", lambda: _step_validate_predictions(config, logger)),
        ("自适应学习", lambda: _step_adaptive_learning(config, logger)),
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
    overall_success = all(
        s.success for s in steps if s.name in ("数据更新", "策略运行")
    )

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
        dashboard_html=args.dashboard_html or "dist/dashboard/index.html",
        dashboard_db=args.dashboard_db or "dist/dashboard/aqsp.db",
        paper_ledger=args.paper_ledger or "data/paper_trades.jsonl",
        notify=args.notify,
        dry_run=args.dry_run,
        enable_debate=args.enable_debate,
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
