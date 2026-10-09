from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
import inspect
import os
from typing import Any

import pandas as pd

from aqsp.data import pit_financial
from aqsp.data.cache import DataCache

_SQLITE_PREFILTERED_SYMBOLS_ENV = "AQSP_SQLITE_PREFILTERED_SYMBOLS"


@dataclass(frozen=True)
class WalkforwardFetchRequest:
    source: str
    symbols: list[str]
    start: str
    end: str
    cache_path: str | None = None
    skip_pit_financials: bool = False
    benchmark_symbol: str | None = None


@dataclass(frozen=True)
class WalkforwardFetchResult:
    frames: dict[str, pd.DataFrame]
    symbols: list[str]
    # 2026-10-06：本次跑批「财务数据是否真的参与了打分」的说明（None = 未跳过 PIT，
    # 走的是 enrich 分支，状态由 source_statuses 表达）。skip 分支必须填入，
    # 否则「quality/value 两维空转」这个事实不会被写进任何产物。
    pit_note: str | None = None


# ---------------------------------------------------------------------------
# 🔴 2026-10-09 订正：哪些维度真的因「跳过 PIT 财务」而空转
#
# 原 note 写的是「quality / value / mean_reversion 三维恒为常数」，**对第三维是错的**。
# 实测口径（`.workbuddy-ai/tools/probe_factor_constant_without_fundamentals.py`：
# 40 只合成标的 × 160 日、**不含任何财务列**，各因子独立跑分后统计互异值/std）：
#
#   momentum             互异值=35  std=0.188  有区分度
#   triple_rise          互异值=17  std=0.174  有区分度
#   quality              互异值= 1  std=0.000  ← 恒为常数（roe/roa/debt_ratio/
#                                                operating_margin 全部缺列 ⇒ 各子项
#                                                返 0.5 ⇒ 常数）
#   value                互异值= 1  std=0.000  ← 恒为常数（pe/pb/dividend_yield 缺列）
#   volume               互异值=40  std=0.165  有区分度
#   high_tight_flag      互异值=40  std=0.097  有区分度
#   mean_reversion(关)   互异值= 1  std=0.000  常数 —— 但成因是 **enabled=False**，
#                                              不是「缺 pe/roe」
#   mean_reversion(开)   互异值=19  std=0.199  ← 有区分度
#
# 即 `mean_reversion` 是**纯价量因子**（只读 close/volume：RSI 超卖 / 乖离率 /
# 量能确认，见 `strategies/mean_reversion.py`），跳过财务对它毫无影响。
#
# 危害（真实且直接落在生产 gate 上）：`stable_plus` 里 **WF-MR1 是唯一
# `enable_mr=True` 且 `mr_weight=0.4` 的臂**。旧 note 会让判读者认定
# 「WF-MR1 的 mr 权重无作用 ⇒ 该臂退化 ⇒ 有效臂只有 7 个 ⇒
# MIN_CSCV_VARIANTS=8 被破坏 ⇒ PBO/DSR 结论不可信」——而这条推理链是假的。
# 反向风险同样存在：`planb_*` 档位给 qual/val 配了非零权重（0.4/0.2），
# 那些权重**确实**在 streaming 下空转，必须保留警示。
# ---------------------------------------------------------------------------

# 依赖财务列、缺列即恒为常数的因子（实测见上表）。
PIT_SKIP_IDLE_FACTORS: tuple[str, ...] = ("quality", "value")

# 只读价格/成交量、**不受**跳过财务影响的因子（用于在 note 里显式排除误读）。
PIT_SKIP_PRICE_VOLUME_FACTORS: tuple[str, ...] = (
    "momentum",
    "triple_rise",
    "volume",
    "mean_reversion",
    "high_tight_flag",
)


def pit_skip_note() -> str:
    """`--skip-pit-financials` 下「哪些维度真的空转」的**单一事实来源**。

    `walkforward_data` 的 skip 分支与 `cli` 报告段的兜底分支共用本函数，
    避免两处文案再次漂移（2026-10-06 起两处各写一份，2026-10-09 才发现两份都错）。
    """
    idle = " / ".join(PIT_SKIP_IDLE_FACTORS)
    unaffected = " / ".join(PIT_SKIP_PRICE_VOLUME_FACTORS)
    return (
        "⚠️ 已跳过 point-in-time 财务补充（`--skip-pit-financials`；"
        "`--streaming` 架构强制，见 cli.py:3691）⇒ 依赖财务列的维度 "
        f"**{idle}** 无 `pe`/`roe` 输入，恒为常数、**未参与打分**；"
        "若变体表给这些维度配了非零权重（如 `planb_*` 档位的 qual/val），"
        "该权重对选股**没有任何作用**。"
        f"⚠️ 价量因子（{unaffected}）**不受影响** —— 它们只读价格/成交量；"
        "`mean_reversion` 尤其如此：它只在被显式启用且权重>0 时参与打分，"
        "启用后提供真实区分度（如 `stable_plus` 的 WF-MR1，mr=0.4）。"
    )


def _attach_benchmark_frame(
    frames: dict[str, pd.DataFrame],
    source: Any,
    benchmark_symbol: str | None,
    start: date,
    end: date,
) -> None:
    if not benchmark_symbol or not hasattr(source, "fetch_index"):
        return
    try:
        benchmark_frames = source.fetch_index([benchmark_symbol], start, end)
    except Exception:
        return
    benchmark_frame = benchmark_frames.get(benchmark_symbol)
    if isinstance(benchmark_frame, pd.DataFrame) and not benchmark_frame.empty:
        frames[benchmark_symbol] = benchmark_frame


def _get_source_with_optional_cache(
    get_source_fn: Callable[..., Any],
    source: str,
    cache: DataCache | None,
) -> Any:
    if cache is None:
        return get_source_fn(source)
    try:
        signature = inspect.signature(get_source_fn)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        parameters = signature.parameters.values()
        if not any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            or (
                parameter.name == "cache"
                and parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }
            )
            for parameter in parameters
        ):
            return get_source_fn(source)
    try:
        return get_source_fn(source, cache=cache)
    except TypeError as exc:
        if "cache" not in str(exc):
            raise
        return get_source_fn(source)


def fetch_walkforward_frames(
    request: WalkforwardFetchRequest,
    *,
    get_source_fn: Callable[[str], Any],
    fetch_frames_for_cli_fn: Callable[..., dict[str, pd.DataFrame]],
    load_csv_fn: Callable[[str], dict[str, pd.DataFrame]],
    fetch_days_fn: Callable[[str, str], int],
    print_fn: Callable[[str], None] = print,
) -> WalkforwardFetchResult:
    source = request.source
    symbols = list(request.symbols)
    benchmark_symbol = str(request.benchmark_symbol or "").strip() or None
    start_d = date.fromisoformat(request.start)
    end_d = date.fromisoformat(request.end)

    if source in {"multi", "akshare", "eastmoney", "tencent"}:
        frames = fetch_frames_for_cli_fn(
            source,
            symbols,
            benchmark_symbol=benchmark_symbol,
            cache_path=request.cache_path or None,
            days=fetch_days_fn(request.start, request.end),
        )
        return WalkforwardFetchResult(frames=frames, symbols=symbols)

    if source == "mootdx":
        src = get_source_fn("mootdx")
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="", count=2000)
        _attach_benchmark_frame(frames, src, benchmark_symbol, start_d, end_d)
        return WalkforwardFetchResult(frames=frames, symbols=symbols)

    if source == "sina":
        src = get_source_fn("sina")
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="")
        _attach_benchmark_frame(frames, src, benchmark_symbol, start_d, end_d)
        return WalkforwardFetchResult(frames=frames, symbols=symbols)

    if source in {"baostock", "sqlite_db"}:
        cache = (
            DataCache(request.cache_path)
            if source == "sqlite_db" and request.cache_path
            else None
        )
        src = _get_source_with_optional_cache(get_source_fn, source, cache)
        if source == "sqlite_db":
            available = src.get_available_symbols()
            symbols = [symbol for symbol in symbols if symbol in available]
            prefiltered = (
                str(os.environ.get(_SQLITE_PREFILTERED_SYMBOLS_ENV, "")).strip().lower()
            )
            if hasattr(src, "get_symbols_with_daily_coverage") and prefiltered not in {
                "1",
                "true",
                "yes",
                "on",
            }:
                symbols = src.get_symbols_with_daily_coverage(
                    symbols,
                    start_d,
                    end_d,
                    min_rows=None,
                )
            print_fn(f"SQLite 数据库中可用且覆盖区间的标的: {len(symbols)} 只")
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="")
        _attach_benchmark_frame(frames, src, benchmark_symbol, start_d, end_d)
        if request.skip_pit_financials:
            # 🔴 2026-10-06：这里必须**显式声明哪些维度因此不参与打分**。
            # 背景：`--streaming` 架构上强制本开关（cli.py:3691，避免 PIT 帧无界占内存），
            # 跳过 ⇒ frames 无 pe/roe 等财务列 ⇒ **quality / value** 恒为常数
            # （实测唯一值=1、std=0；见 `PIT_SKIP_IDLE_FACTORS` 上方实测表）。
            # 若变体对这些维度配了非零权重（`planb_*` 档位），该权重对选股**毫无作用**，
            # 而报告的逐变体表仍会如实显示它。
            # ⚠️ 2026-10-09 订正：旧文案把 `mean_reversion` 也列为空转维度，**是错的**
            # —— 它是纯价量因子，与财务列无关。详见 `pit_skip_note()` 上方长注释。
            # 旧实现在此直接 return，把「财务数据源是否可用」这个关键事实
            # **完全不上报**（pit_result.source_statuses 的打印在其后，永不执行）
            # ⇒ 只能被人肉考古发现。现改为：既打印、也把状态挂到返回值上，
            # 供 cli 写进 gate report（见 WalkforwardFetchResult.pit_note）。
            skipped_note = pit_skip_note()
            print_fn("已跳过 point-in-time 财务补充，仅使用价格数据跑 gate")
            print_fn(skipped_note)
            return WalkforwardFetchResult(
                frames=frames, symbols=symbols, pit_note=skipped_note
            )
        print_fn(
            f"正在获取 {len(symbols)} 只股票 {request.start} ~ {request.end} 的 point-in-time 财务数据..."
        )
        pit_result = pit_financial.enrich_ohlcv_with_pit_financials(
            frames,
            symbols,
            start_d,
            end_d,
            cache=DataCache(),
        )
        print_fn(f"财务数据合并完成: {pit_result.financial_symbol_count} 只有财务数据")
        if pit_result.disclosure_symbol_count:
            print_fn(f"Tushare 披露日覆盖完成: {pit_result.disclosure_symbol_count} 只")
        for status in getattr(pit_result, "source_statuses", ()):
            print_fn(f"PIT源 {status.source_id}: {status.status} - {status.message}")
        return WalkforwardFetchResult(frames=pit_result.frames, symbols=symbols)

    return WalkforwardFetchResult(frames=load_csv_fn(source), symbols=symbols)
