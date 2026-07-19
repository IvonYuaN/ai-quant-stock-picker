from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, wait
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
import pandas as pd

from aqsp.data.source import DataSource, OhlcvFrame
from aqsp.core.errors import DataError
from aqsp.core.time import SHANGHAI_TZ, today_shanghai, is_market_open, now_shanghai
from aqsp.core.errors import MissingDataError
from aqsp.data.source_readiness import (
    source_role_for_workload,
    workload_guard_message,
)

_logger = logging.getLogger(__name__)

_INTRADAY_MAX_AGE_SECONDS: dict[str, int] = {
    "1": 300,
    "5": 900,
    "15": 1800,
    "30": 3600,
    "60": 7200,
}
_MAX_FUTURE_BAR_SECONDS = 120
_COMPOSITE_SOURCES = frozenset({"auto", "local_first", "online_first", "multi"})
_A_SHARE_SESSION_MINUTES = 240
_DEFAULT_FETCH_DEADLINE_SECONDS = 30.0
_DEFAULT_FETCH_MAX_WORKERS = 4
_DEFAULT_FETCH_BATCH_SIZE = 64


def _chunked(values: list[str], size: int) -> list[list[str]]:
    """Split symbols into bounded source requests."""
    return [values[index : index + size] for index in range(0, len(values), size)]


def _a_share_elapsed_trading_minutes(timestamp: datetime) -> int:
    """Return completed A-share session minutes without counting the lunch break."""
    minute_of_day = timestamp.hour * 60 + timestamp.minute
    morning_start = 9 * 60 + 30
    morning_end = 11 * 60 + 30
    afternoon_start = 13 * 60
    afternoon_end = 15 * 60
    if minute_of_day <= morning_start:
        return 1
    if minute_of_day <= morning_end:
        return min(120, minute_of_day - morning_start)
    if minute_of_day < afternoon_start:
        return 120
    if minute_of_day <= afternoon_end:
        return min(240, 120 + minute_of_day - afternoon_start)
    return _A_SHARE_SESSION_MINUTES


@dataclass(frozen=True)
class FrameProvenance:
    """Traceability for one homogeneous data frame."""

    source: str
    workload: str
    fetched_at: str
    timestamp_source: str
    freshness: str
    data_date: str = ""


@dataclass(frozen=True)
class OverlayProvenance:
    """Traceability for a daily frame with today's intraday overlay."""

    intraday: FrameProvenance
    historical: FrameProvenance | None
    benchmark: FrameProvenance | None = None


@dataclass(frozen=True)
class IntradayOverlayResult:
    """Merged frames plus the explicit coverage required for a live decision."""

    frames: dict[str, pd.DataFrame]
    requested_symbols: tuple[str, ...]
    covered_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    candidate_requested_symbols: tuple[str, ...] = ()
    candidate_covered_symbols: tuple[str, ...] = ()
    candidate_missing_symbols: tuple[str, ...] = ()
    benchmark_requested_symbols: tuple[str, ...] = ()
    benchmark_covered_symbols: tuple[str, ...] = ()
    benchmark_missing_symbols: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing_symbols

    @property
    def candidate_complete(self) -> bool:
        return not self.candidate_missing_symbols

    @property
    def benchmark_complete(self) -> bool:
        return not self.benchmark_missing_symbols


class IntradayService:
    def __init__(
        self,
        source: DataSource,
        *,
        allow_historical_replay: bool = False,
        fetch_deadline_seconds: float = _DEFAULT_FETCH_DEADLINE_SECONDS,
        fetch_max_workers: int = _DEFAULT_FETCH_MAX_WORKERS,
        fetch_batch_size: int = _DEFAULT_FETCH_BATCH_SIZE,
    ) -> None:
        source_name = str(getattr(source, "name", "") or "").strip()
        guard_message = workload_guard_message(source_name, "live_short")
        if guard_message:
            raise DataError(guard_message)
        if (
            source_name not in _COMPOSITE_SOURCES
            and source_role_for_workload(source_name, "live_short") != "realtime"
        ):
            raise DataError(
                f"数据源 {source_name} 仅可作为 observation 层，不能形成正式盘中数据"
            )
        self.source = source
        self.allow_historical_replay = bool(allow_historical_replay)
        if fetch_deadline_seconds <= 0:
            raise ValueError("fetch_deadline_seconds 必须大于 0")
        if fetch_max_workers <= 0:
            raise ValueError("fetch_max_workers 必须大于 0")
        if fetch_batch_size <= 0:
            raise ValueError("fetch_batch_size 必须大于 0")
        self.fetch_deadline_seconds = float(fetch_deadline_seconds)
        self.fetch_max_workers = int(fetch_max_workers)
        self.fetch_batch_size = int(fetch_batch_size)

    def get_intraday_bars(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
        *,
        index_symbols: Collection[str] = (),
        target_date: date | None = None,
    ) -> dict[str, OhlcvFrame]:
        if not symbols:
            raise DataError("未请求分时标的")
        if (
            target_date is not None
            and target_date != now_shanghai().date()
            and not self.allow_historical_replay
        ):
            raise DataError(
                "live_short 仅允许当前交易日分时；历史日期必须显式启用 replay 模式"
            )
        result = self._fetch_intraday_with_symbol_isolation(
            symbols,
            period,
            index_symbols=index_symbols,
        )
        missing = [s for s in symbols if s not in result or result[s].empty]
        if missing and len(missing) == len(symbols):
            raise MissingDataError(symbols[0], reason=f"分时数据全部缺失: {missing}")
        if missing:
            _logger.warning(
                "数据源 %s 分时获取不完整，跳过 %d/%d 个标的: %s",
                self.source.name,
                len(missing),
                len(symbols),
                missing[:20],
            )
        validated: dict[str, OhlcvFrame] = {}
        rejected_reasons: list[str] = []
        replay = target_date is not None and target_date != now_shanghai().date()
        for symbol, frame in result.items():
            try:
                _validate_live_bar_freshness(
                    symbol,
                    frame,
                    period,
                    target_date=target_date,
                )
                _annotate_live_intraday_provenance(
                    symbol,
                    frame,
                    source=self.source,
                    fetched_at=frame.attrs.get("fetched_at"),
                    workload="walkforward" if replay else "live_short",
                    freshness="historical" if replay else "fresh",
                )
            except DataError as exc:
                _logger.warning(
                    "数据源 %s 分时 freshness 拒绝 %s: %s",
                    self.source.name,
                    symbol,
                    exc,
                )
                rejected_reasons.append(str(exc))
                continue
            validated[symbol] = frame
        if not validated:
            reason = "分时数据全部缺失或已过期"
            if rejected_reasons:
                reason += f": {rejected_reasons[0]}"
            raise MissingDataError(symbols[0], reason=reason)
        return validated

    def _fetch_intraday_with_symbol_isolation(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"],
        *,
        index_symbols: Collection[str] = (),
    ) -> dict[str, OhlcvFrame]:
        index_set = {str(symbol) for symbol in index_symbols if str(symbol)}
        requested = list(
            dict.fromkeys(str(symbol) for symbol in symbols if str(symbol))
        )
        stock_symbols = [symbol for symbol in requested if symbol not in index_set]
        benchmark_symbols = [symbol for symbol in requested if symbol in index_set]
        jobs: list[tuple[tuple[str, ...], str]] = [
            (tuple(batch), "fetch_intraday")
            for batch in _chunked(stock_symbols, self.fetch_batch_size)
        ]
        if benchmark_symbols:
            jobs.extend(
                (tuple(batch), "fetch_index_intraday")
                for batch in _chunked(benchmark_symbols, self.fetch_batch_size)
            )
        if not jobs:
            return {}

        setter = getattr(self.source, "set_workload", None)
        if callable(setter):
            setter("live_short")
        executor = ThreadPoolExecutor(
            max_workers=min(self.fetch_max_workers, len(jobs)),
            thread_name_prefix="aqsp-intraday",
        )
        futures = {
            executor.submit(self._fetch_intraday_batch, batch, period, method_name): batch
            for batch, method_name in jobs
        }
        try:
            done, pending = wait(futures, timeout=self.fetch_deadline_seconds)
            for future in pending:
                future.cancel()
            result: dict[str, OhlcvFrame] = {}
            for future in done:
                batch = futures[future]
                try:
                    frames = future.result()
                except Exception as exc:
                    _logger.warning(
                        "数据源 %s 分时批次跳过 %s: %s",
                        self.source.name,
                        ",".join(batch),
                        exc,
                    )
                    continue
                for symbol, frame in frames.items():
                    if frame is not None and not frame.empty:
                        result[symbol] = frame
            if pending:
                _logger.warning(
                    "数据源 %s 分时共享 deadline %.1fs 到期，跳过 %d/%d 个批次",
                    self.source.name,
                    self.fetch_deadline_seconds,
                    len(pending),
                    len(futures),
                )
            return result
        finally:
            # Do not wait again after the shared deadline. Requests are independently
            # bounded by their adapter; incomplete symbols remain explicit missing.
            executor.shutdown(wait=False, cancel_futures=True)
            if callable(setter):
                setter(None)

    def _fetch_intraday_batch(
        self,
        symbols: tuple[str, ...],
        period: Literal["1", "5", "15", "30", "60"],
        method_name: str,
    ) -> dict[str, OhlcvFrame]:
        fetch_method = getattr(self.source, method_name, None)
        if not callable(fetch_method):
            if method_name == "fetch_index_intraday":
                _logger.warning(
                    "数据源 %s 不支持指数分时接口，benchmark 不能冒充股票分时",
                    self.source.name,
                )
                return {}
            fetch_method = self.source.fetch_intraday
        try:
            result = fetch_method(list(symbols), period)
        except Exception:
            # A few adapters reject batched requests even though their single-symbol
            # endpoint works. Retry only this failed batch so the normal path keeps
            # bounded fan-out while preserving partial-symbol isolation.
            if method_name == "fetch_index_intraday" or len(symbols) == 1:
                raise
            result = {}
            for symbol in symbols:
                try:
                    result.update(fetch_method([symbol], period))
                except Exception as exc:
                    _logger.warning(
                        "数据源 %s 分时降级跳过标的 %s: %s",
                        self.source.name,
                        symbol,
                        exc,
                    )
        if not result:
            raise MissingDataError(symbols[0], reason="分时批次返回为空")
        return {
            str(symbol): frame
            for symbol, frame in result.items()
            if frame is not None and not frame.empty and str(symbol) in symbols
        }

    def synthesize_daily_from_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
        *,
        target_date: date | None = None,
        index_symbols: Collection[str] = (),
    ) -> dict[str, OhlcvFrame]:
        intraday_data = self.get_intraday_bars(
            symbols,
            period,
            target_date=target_date,
            index_symbols=index_symbols,
        )
        result = {}

        for symbol, df in intraday_data.items():
            if df.empty:
                continue
            result[symbol] = self._synthesize_single_symbol_daily(
                symbol,
                df,
                target_date=target_date,
            )

        if not result:
            raise MissingDataError(
                symbols[0], reason="所有标的分时数据缺失,无法合成日K"
            )
        return result

    def merge_intraday_bar_into_daily(
        self,
        daily_data: dict[str, pd.DataFrame],
        symbols: list[str],
        *,
        period: Literal["1", "5", "15", "30", "60"] = "5",
        target_date: date | None = None,
        index_symbols: Collection[str] = (),
    ) -> dict[str, pd.DataFrame]:
        trade_day = target_date or today_shanghai()
        intraday_data = self.get_intraday_bars(
            symbols,
            period,
            target_date=trade_day,
            index_symbols=index_symbols,
        )
        merged: dict[str, pd.DataFrame] = {}
        first_error: Exception | None = None

        for symbol in symbols:
            daily = daily_data.get(symbol)
            intraday_frame = intraday_data.get(symbol)
            if intraday_frame is None or intraday_frame.empty:
                first_error = first_error or MissingDataError(
                    symbol, reason="缺少当日分时数据"
                )
                continue
            try:
                synthesized = self._synthesize_single_symbol_daily(
                    symbol,
                    intraday_frame,
                    target_date=trade_day,
                )
            except Exception as exc:
                first_error = first_error or exc
                _logger.warning(
                    "数据源 %s 分时合成跳过坏标的 %s: %s",
                    self.source.name,
                    symbol,
                    exc,
                )
                continue
            merged[symbol] = self._merge_single_symbol_daily(
                daily,
                synthesized,
                trade_day=trade_day,
            )
        if not merged:
            if first_error is not None:
                raise first_error
            raise MissingDataError(symbols[0], reason="所有标的分时数据缺失")
        return merged

    def merge_intraday_bar_into_daily_with_coverage(
        self,
        daily_data: dict[str, pd.DataFrame],
        symbols: list[str],
        *,
        period: Literal["1", "5", "15", "30", "60"] = "5",
        target_date: date | None = None,
        index_symbols: Collection[str] = (),
    ) -> IntradayOverlayResult:
        """Merge live bars and retain missing symbols instead of hiding them."""
        requested = tuple(dict.fromkeys(symbol for symbol in symbols if symbol))
        index_set = {str(symbol) for symbol in index_symbols if str(symbol)}
        try:
            frames = self.merge_intraday_bar_into_daily(
                daily_data,
                list(requested),
                period=period,
                target_date=target_date,
                index_symbols=index_symbols,
            )
        except MissingDataError:
            frames = {}
        covered = tuple(symbol for symbol in requested if symbol in frames)
        missing = tuple(symbol for symbol in requested if symbol not in frames)
        candidate_requested = tuple(
            symbol for symbol in requested if symbol not in index_set
        )
        candidate_covered = tuple(
            symbol for symbol in candidate_requested if symbol in frames
        )
        candidate_missing = tuple(
            symbol for symbol in candidate_requested if symbol not in frames
        )
        benchmark_requested = tuple(
            symbol for symbol in requested if symbol in index_set
        )
        benchmark_covered = tuple(
            symbol for symbol in benchmark_requested if symbol in frames
        )
        benchmark_missing = tuple(
            symbol for symbol in benchmark_requested if symbol not in frames
        )
        return IntradayOverlayResult(
            frames=frames,
            requested_symbols=requested,
            covered_symbols=covered,
            missing_symbols=missing,
            candidate_requested_symbols=candidate_requested,
            candidate_covered_symbols=candidate_covered,
            candidate_missing_symbols=candidate_missing,
            benchmark_requested_symbols=benchmark_requested,
            benchmark_covered_symbols=benchmark_covered,
            benchmark_missing_symbols=benchmark_missing,
        )

    def _merge_single_symbol_daily(
        self,
        daily: pd.DataFrame | None,
        intraday_daily: pd.DataFrame,
        *,
        trade_day: date,
    ) -> pd.DataFrame:
        intraday_daily = intraday_daily.copy()
        intraday_provenance = _frame_provenance_from_attrs(
            intraday_daily,
            symbol=str(intraday_daily["symbol"].iloc[0])
            if "symbol" in intraday_daily.columns and not intraday_daily.empty
            else "unknown",
            default_freshness="fresh",
        )
        intraday_day_text = trade_day.isoformat()
        if daily is None or daily.empty:
            merged = intraday_daily.reset_index(drop=True)
            return _attach_overlay_provenance(
                merged,
                OverlayProvenance(intraday=intraday_provenance, historical=None),
            )

        base = daily.copy()
        historical_provenance = _require_historical_provenance(
            base,
            str(intraday_daily["symbol"].iloc[0])
            if "symbol" in intraday_daily.columns and not intraday_daily.empty
            else "unknown",
        )
        benchmark_provenance = daily.attrs.get("benchmark_provenance")
        if not isinstance(benchmark_provenance, FrameProvenance):
            benchmark_provenance = None
        base["date"] = pd.to_datetime(base["date"], errors="coerce").dt.strftime(
            "%Y-%m-%d"
        )
        base = base.dropna(subset=["date"])
        # A live overlay may keep prior history for indicators, but future rows
        # are look-ahead data and must never survive the merge.
        base = base[base["date"] < intraday_day_text]
        merged = pd.concat([base, intraday_daily], ignore_index=True)
        merged = merged.sort_values("date").reset_index(drop=True)
        return _attach_overlay_provenance(
            merged,
            OverlayProvenance(
                intraday=intraday_provenance,
                historical=historical_provenance,
                benchmark=benchmark_provenance,
            ),
        )

    def _synthesize_single_symbol_daily(
        self,
        symbol: str,
        df: pd.DataFrame,
        *,
        target_date: date | None = None,
    ) -> pd.DataFrame:
        normalized = df.copy()
        source_provenance = _frame_provenance_from_attrs(
            normalized,
            symbol=symbol,
            default_freshness="fresh",
        )
        normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
        normalized = normalized.dropna(subset=["date"]).sort_values("date")
        resolved_date = target_date
        if resolved_date is None:
            if normalized.empty:
                raise MissingDataError(symbol, reason="分时数据为空")
            resolved_date = normalized["date"].dt.date.iloc[-1]
        normalized = normalized[
            normalized["date"].dt.date == resolved_date
        ].reset_index(drop=True)
        if normalized.empty:
            raise MissingDataError(
                symbol,
                reason=f"分时数据不含 {resolved_date.isoformat()} 当日 bar",
            )

        for column in ("open", "high", "low", "close", "volume"):
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        if normalized[["open", "high", "low", "close", "volume"]].isna().any().any():
            raise DataError(f"分时数据存在无效数值: {symbol}")

        first_bar = normalized.iloc[0]
        last_bar = normalized.iloc[-1]
        elapsed_minutes = _a_share_elapsed_trading_minutes(
            pd.Timestamp(last_bar["date"]).to_pydatetime()
        )
        amount_series = (
            pd.to_numeric(normalized["amount"], errors="coerce")
            if "amount" in normalized.columns
            else pd.Series([], dtype=float)
        )
        amount = (
            float(amount_series.fillna(0.0).sum()) if not amount_series.empty else 0.0
        )
        name_value = symbol
        if "name" in normalized.columns and not normalized["name"].dropna().empty:
            name_value = str(normalized["name"].dropna().iloc[-1])

        synthesized = pd.DataFrame(
            {
                "date": [resolved_date.isoformat()],
                "symbol": [symbol],
                "name": [name_value],
                "open": [float(first_bar["open"])],
                "high": [float(normalized["high"].max())],
                "low": [float(normalized["low"].min())],
                "close": [float(last_bar["close"])],
                "volume": [float(normalized["volume"].sum())],
                "amount": [amount],
                "suspended": [False],
                "limit_up": [0.0],
                "limit_down": [0.0],
                "adj_factor": [1.0],
                "intraday_elapsed_minutes": [elapsed_minutes],
                "intraday_session_minutes": [_A_SHARE_SESSION_MINUTES],
            }
        )
        return _attach_frame_provenance(synthesized, source_provenance)

    def merge_intraday_with_daily(
        self,
        daily_data: dict[str, pd.DataFrame],
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}

        intraday = self.synthesize_daily_from_intraday(symbols, period)

        for symbol in symbols:
            daily = daily_data.get(symbol)
            if daily is None or daily.empty:
                if symbol in intraday:
                    result[symbol] = _attach_overlay_provenance(
                        intraday[symbol].copy(),
                        OverlayProvenance(
                            intraday=_frame_provenance_from_attrs(
                                intraday[symbol],
                                symbol=symbol,
                                default_freshness="fresh",
                            ),
                            historical=None,
                        ),
                    )
                continue

            if symbol in intraday:
                today_intraday = intraday[symbol].iloc[0]
                today_date = date.fromisoformat(str(today_intraday["date"])[:10])
                result[symbol] = self._merge_single_symbol_daily(
                    daily,
                    intraday[symbol],
                    trade_day=today_date,
                )

        return result

    def get_current_bar(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
        *,
        target_date: date | None = None,
    ) -> dict[str, pd.Series]:
        intraday = self.get_intraday_bars(
            symbols,
            period,
            target_date=target_date,
        )
        result = {}

        for symbol, df in intraday.items():
            if not df.empty:
                result[symbol] = df.iloc[-1]

        if not result:
            raise MissingDataError(symbols[0], reason="所有标的当前Bar数据缺失")
        return result


def _validate_live_bar_freshness(
    symbol: str,
    frame: OhlcvFrame,
    period: Literal["1", "5", "15", "30", "60"],
    *,
    now: datetime | None = None,
    target_date: date | None = None,
) -> None:
    """Reject stale bars unless the caller explicitly requests a replay date."""
    if frame.empty or "date" not in frame.columns:
        raise DataError(f"分时数据缺少时间列: {symbol}")
    parsed = pd.to_datetime(frame["date"], errors="coerce")
    if parsed.dropna().empty:
        raise DataError(f"分时数据时间无效: {symbol}")
    latest = parsed.dropna().max()
    current = now or now_shanghai()
    current = (
        current.replace(tzinfo=SHANGHAI_TZ)
        if current.tzinfo is None
        else current.astimezone(SHANGHAI_TZ)
    )
    expected_date = target_date or current.date()
    if expected_date > current.date():
        raise DataError(
            f"分时目标日期不能晚于当前日期: {symbol} target={expected_date.isoformat()} "
            f"current={current.date().isoformat()}"
        )
    if latest.date() != expected_date:
        raise DataError(
            f"分时最新 bar 非目标交易日: {symbol} latest={latest.date().isoformat()} "
            f"expected={expected_date.isoformat()}，缺少当日 bar"
        )
    # An explicit non-current target is a historical replay/fixture path. Live
    # callers omit target_date, so a previous-day bar cannot enter live_short.
    if target_date is not None and target_date != current.date():
        return
    if latest.tzinfo is None:
        latest = latest.tz_localize(SHANGHAI_TZ)
    else:
        latest = latest.tz_convert(SHANGHAI_TZ)
    age_seconds = (current - latest.to_pydatetime()).total_seconds()
    if age_seconds < -_MAX_FUTURE_BAR_SECONDS:
        raise DataError(f"分时最新 bar 时间超前当前时间: {symbol} {latest.isoformat()}")
    # Lunch break and post-close have no new bars by design.
    clock = current.time()
    in_lunch_break = clock.replace(tzinfo=None) >= clock.replace(
        hour=11, minute=30, second=0, microsecond=0, tzinfo=None
    ) and clock.replace(tzinfo=None) < clock.replace(
        hour=13, minute=0, second=0, microsecond=0, tzinfo=None
    )
    if not is_market_open(current) or in_lunch_break:
        return
    max_age = _INTRADAY_MAX_AGE_SECONDS[period]
    if age_seconds > max_age:
        raise DataError(
            f"分时最新 bar 已过期: {symbol} age={age_seconds:.0f}s max={max_age}s"
        )


def _annotate_live_intraday_provenance(
    symbol: str,
    frame: pd.DataFrame,
    *,
    source: DataSource,
    fetched_at: object | None,
    workload: Literal["live_short", "walkforward"] = "live_short",
    freshness: str = "fresh",
) -> None:
    """Attach verifiable live provenance before any frame is copied or concatenated."""
    source_name = str(frame.attrs.get("source_name", "") or "").strip()
    source_provenance = getattr(source, "last_used_sources", {})
    if not source_name and isinstance(source_provenance, dict):
        source_name = str(source_provenance.get(symbol, "") or "").strip()
    if not source_name and str(getattr(source, "name", "")).strip() != "multi":
        source_name = str(getattr(source, "name", "") or "").strip()
    if not source_name or source_name in _COMPOSITE_SOURCES:
        raise DataError(f"实时 workload 标的 {symbol} 缺少可验证 provenance，拒绝继续")
    expected_role = "realtime" if workload == "live_short" else "historical"
    if source_role_for_workload(source_name, workload) != expected_role:
        raise DataError(
            f"{workload} workload 标的 {symbol} 来源 {source_name} 角色不可接受"
        )

    frame_workload = str(frame.attrs.get("workload", workload) or "").strip()
    allowed_frame_workloads = {"", workload}
    if workload == "walkforward":
        # The fetch adapter is still invoked through the live bar API, so some
        # adapters may attach the transport context before replay is relabeled.
        allowed_frame_workloads.add("live_short")
    if frame_workload not in allowed_frame_workloads:
        raise DataError(
            f"{workload} workload 标的 {symbol} workload 不匹配: "
            f"{frame_workload or 'unknown'}"
        )
    fetched = _normalize_fetched_at(
        fetched_at or frame.attrs.get("fetched_at") or now_shanghai().isoformat(),
        field=f"分时 {symbol} fetched_at",
    )
    timestamp_source = str(
        frame.attrs.get("timestamp_source", "")
        or _first_non_empty_column_value(frame, "timestamp_source")
        or "bar_time"
    ).strip()
    if not timestamp_source:
        raise DataError(f"实时 workload 标的 {symbol} 缺少 timestamp_source，拒绝继续")
    frame.attrs.update(
        {
            "source_name": source_name,
            "source": source_name,
            "workload": workload,
            "fetched_at": fetched,
            "timestamp_source": timestamp_source,
            "freshness": freshness,
        }
    )
    provenance = FrameProvenance(
        source=source_name,
        workload=workload,
        fetched_at=fetched,
        timestamp_source=timestamp_source,
        freshness=freshness,
    )
    frame.attrs["provenance"] = provenance
    frame.attrs["typed_provenance"] = provenance


def _frame_provenance_from_attrs(
    frame: pd.DataFrame,
    *,
    symbol: str,
    default_freshness: str,
) -> FrameProvenance:
    source = str(
        frame.attrs.get("source_name", "")
        or frame.attrs.get("source", "")
        or _first_non_empty_column_value(frame, "source")
    ).strip()
    workload = str(
        frame.attrs.get("workload", "")
        or _first_non_empty_column_value(frame, "workload")
    ).strip()
    fetched_at = frame.attrs.get("fetched_at") or _first_non_empty_column_value(
        frame, "fetched_at"
    )
    timestamp_source = str(
        frame.attrs.get("timestamp_source", "")
        or _first_non_empty_column_value(frame, "timestamp_source")
    ).strip()
    freshness = str(
        frame.attrs.get("freshness", "")
        or frame.attrs.get("freshness_status", "")
        or default_freshness
    ).strip()
    if not source or not workload or not fetched_at or not timestamp_source:
        missing = [
            name
            for name, value in (
                ("source", source),
                ("workload", workload),
                ("fetched_at", fetched_at),
                ("timestamp_source", timestamp_source),
            )
            if not str(value or "").strip()
        ]
        raise DataError(
            f"{symbol} 缺少可验证 provenance: {', '.join(missing)}，拒绝继续"
        )
    normalized_fetched_at = _normalize_fetched_at(
        fetched_at,
        field=f"{symbol} fetched_at",
    )
    data_date = ""
    if "date" in frame.columns and not frame.empty:
        parsed = pd.to_datetime(frame["date"], errors="coerce").dropna()
        if not parsed.empty:
            data_date = parsed.max().date().isoformat()
    return FrameProvenance(
        source=source,
        workload=workload,
        fetched_at=normalized_fetched_at,
        timestamp_source=timestamp_source,
        freshness=freshness or default_freshness,
        data_date=data_date,
    )


def _require_historical_provenance(
    frame: pd.DataFrame,
    symbol: str,
) -> FrameProvenance:
    """Do not let an untraceable historical base enter a live overlay."""
    try:
        return _frame_provenance_from_attrs(
            frame,
            symbol=symbol,
            default_freshness="historical",
        )
    except DataError as exc:
        raise DataError(f"历史日线 {symbol} provenance 不完整: {exc}") from exc


def _attach_frame_provenance(
    frame: pd.DataFrame,
    provenance: FrameProvenance,
) -> pd.DataFrame:
    frame.attrs.update(
        {
            "provenance": provenance,
            "typed_provenance": provenance,
            "source_name": provenance.source,
            "source": provenance.source,
            "workload": provenance.workload,
            "fetched_at": provenance.fetched_at,
            "timestamp_source": provenance.timestamp_source,
            "freshness": provenance.freshness,
        }
    )
    return frame


def _attach_overlay_provenance(
    frame: pd.DataFrame,
    provenance: OverlayProvenance,
) -> pd.DataFrame:
    frame.attrs.update(
        {
            "provenance": provenance,
            "typed_provenance": provenance,
            "intraday_provenance": provenance.intraday,
            "historical_provenance": provenance.historical,
            "benchmark_provenance": provenance.benchmark,
            # Keep existing live_short consumers compatible with the overlay's
            # current-day source while exposing the historical side explicitly.
            "source_name": provenance.intraday.source,
            "source": provenance.intraday.source,
            "workload": provenance.intraday.workload,
            "fetched_at": provenance.intraday.fetched_at,
            "timestamp_source": provenance.intraday.timestamp_source,
            "freshness": provenance.intraday.freshness,
            "overlay": "intraday",
        }
    )
    if provenance.historical is not None:
        frame.attrs.update(
            {
                "historical_source": provenance.historical.source,
                "historical_workload": provenance.historical.workload,
                "historical_fetched_at": provenance.historical.fetched_at,
                "historical_timestamp_source": provenance.historical.timestamp_source,
                "historical_freshness": provenance.historical.freshness,
            }
        )
    return frame


def _first_non_empty_column_value(frame: pd.DataFrame, column: str) -> str:
    if column not in frame.columns:
        return ""
    for value in frame[column].tolist():
        text = str(value or "").strip()
        if text and text.lower() != "nan":
            return text
    return ""


def _normalize_fetched_at(value: object, *, field: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise DataError(f"{field} 无效: {raw or 'empty'}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataError(f"{field} 缺少时区: {raw}")
    return parsed.astimezone(SHANGHAI_TZ).isoformat()
