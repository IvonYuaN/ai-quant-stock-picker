from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Literal
import pandas as pd

from aqsp.data.source import (
    DataSource,
    OhlcvFrame,
    apply_limit_suspended_adj,
    require_fetched_frame,
    require_non_empty_fetch_result,
)
from aqsp.data.cache import DataCache
from aqsp.core.errors import DataError
from aqsp.core.http import build_http_session, get_http_config
from aqsp.core.time import now_shanghai
from aqsp.data.quote_metadata import (
    parse_legacy_quote_timestamp,
    quote_timestamp_metadata,
)

_logger = logging.getLogger("aqsp.data.tencent")

TENCENT_SIMPLE_QUOTE_URL = "http://qt.gtimg.cn/q=s_{symbol}"
TENCENT_FULL_QUOTE_URL = "http://qt.gtimg.cn/q={market}{symbol}"
TENCENT_BATCH_QUOTE_URL = "http://qt.gtimg.cn/q={symbols}"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
# 分时（当日分钟线）。不要改回 `appstock/app/minute/query`：该路径已被腾讯 WAF
# 按路径封禁（生产机实测 http/https、sh/sz 一律 501，返回 WAF 拦截页而非 JSON），
# 而同一主机的 `appstock/app/day/query` 路径正常返回 200 与当日逐分钟数据。
# day/query 固定回多个交易日（`n` 参数不生效），故必须按日期挑当日场次，
# 见 _tencent_intraday_today_minutes。
TENCENT_INTRADAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query"

TENCENT_QUOTE_FIELD_LIMIT_UP = 47
TENCENT_QUOTE_FIELD_LIMIT_DOWN = 48

_REQUEST_DELAY = 0.3
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_DEFAULT_DAILY_FETCH_WORKERS = 8

# 分时端点级故障短路。一个源彻底挂掉时（被 WAF 拦、被限流、域名不可达），若仍按
# 每只标的重试 3 次（退避 2s+4s ≈ 6s），一批 20 只就要 120s，而 IntradayService 的
# **整批**共享预算只有 90s —— 结果是一个源把预算吃光，兜底源根本没轮到
# （2026-09-18 实测：90s 内只来得及试 8 只标的，257/257 全部跳过）。
# 故连续 _ENDPOINT_DOWN_THRESHOLD 只端点级失败即短路，冷却期内直接快速失败，
# 把预算让给竞速同伴与 deferred 兜底源。
_ENDPOINT_DOWN_THRESHOLD = 3
_ENDPOINT_DOWN_COOLDOWN = 30.0


class TencentEndpointDownError(DataError):
    """端点级故障：连接失败、HTTP 非 200，或响应体不是 JSON（WAF 拦截页）。

    与「该标的没有分时数据」严格区分：前者说明整条链路不可用，后者只是单只
    标的问题（停牌/退市）。只有前者计入短路计数。
    """


def _get_market_prefix(symbol: str, *, is_index: bool = False) -> str:
    if is_index:
        # 指数市场归属：399/390 深交所、899 北交所、其余（000/930 等）上交所
        if symbol.startswith(("399", "390")):
            return "sz"
        if symbol.startswith("899"):
            return "bj"
        return "sh"
    if symbol.startswith("920"):
        return "bj"
    if symbol.startswith("6"):
        return "sh"
    return "sz"


def _configured_daily_fetch_workers(requested_count: int) -> int:
    raw = os.getenv("AQSP_TENCENT_DAILY_FETCH_WORKERS", "").strip()
    try:
        configured = int(raw) if raw else _DEFAULT_DAILY_FETCH_WORKERS
    except ValueError:
        configured = _DEFAULT_DAILY_FETCH_WORKERS
    return max(1, min(configured, max(1, requested_count)))


class TencentSource(DataSource):
    name: str = "tencent"

    def __init__(self, cache: DataCache | None = None) -> None:
        self._session = build_http_session(
            config=get_http_config(),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        self.cache = cache or DataCache()
        self._last_request_ts: float = 0.0
        self._active_workload: str | None = None
        # 分时端点短路状态。按实例而非模块级持有：盘中一次刷新就是一个进程、
        # 一个实例，4 个 worker 线程共享它，作用域正好等于「本次刷新」，
        # 不需要引入跨进程全局状态。
        self._endpoint_down_lock = threading.Lock()
        self._endpoint_down_streak = 0
        self._endpoint_down_until = 0.0

    def set_workload(self, workload: str | None) -> None:
        """Set provenance context for cache-backed runtime fetches."""
        self._active_workload = workload

    def _cache_workload(self) -> str | None:
        return getattr(self, "_active_workload", None)

    def _annotate_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        frame.attrs["source_name"] = self.name
        frame.attrs["source"] = self.name
        workload = self._cache_workload()
        if workload:
            frame.attrs["workload"] = workload
            frame.attrs["fetched_at"] = str(
                frame.attrs.get("fetched_at") or now_shanghai().isoformat()
            )
            frame.attrs["timestamp_source"] = str(
                frame.attrs.get("timestamp_source") or "received_at"
            )
        return frame

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < _REQUEST_DELAY:
            time.sleep(_REQUEST_DELAY - elapsed)
        self._last_request_ts = time.monotonic()

    def _endpoint_down(self) -> bool:
        """分时端点是否处于短路冷却期。"""
        with self._endpoint_down_lock:
            return time.monotonic() < self._endpoint_down_until

    def _record_endpoint_rejection(self) -> bool:
        """记一次确定性端点拒绝；返回短路是否已触发。"""
        global_open = False
        with self._endpoint_down_lock:
            self._endpoint_down_streak += 1
            if self._endpoint_down_streak >= _ENDPOINT_DOWN_THRESHOLD:
                self._endpoint_down_until = time.monotonic() + _ENDPOINT_DOWN_COOLDOWN
                global_open = True
        if global_open:
            _logger.warning(
                "tencent 分时端点连续 %d 次确定性拒绝，短路 %.0fs 内快速失败，"
                "把预算让给竞速同伴与兜底源",
                _ENDPOINT_DOWN_THRESHOLD,
                _ENDPOINT_DOWN_COOLDOWN,
            )
        return global_open

    def _record_endpoint_success(self) -> None:
        with self._endpoint_down_lock:
            self._endpoint_down_streak = 0
            self._endpoint_down_until = 0.0

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        pending: list[str] = []
        for symbol in symbols:
            cached = self.cache.get_ohlcv(
                symbol,
                start,
                end,
                price_mode=adjust or "raw",
                source=self.name,
                workload=self._cache_workload(),
            )
            if cached is not None and not cached.empty:
                out[symbol] = self._annotate_frame(cached)
                continue
            pending.append(symbol)

        # Fetch network data concurrently, then normalize/cache sequentially.
        # This keeps SQLite/cache writes deterministic while avoiding a full
        # live batch deadline spent on the 0.3s per-symbol throttle.
        workers = _configured_daily_fetch_workers(len(pending))
        fetched: dict[str, pd.DataFrame] = {}
        if pending:
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="aqsp-tencent-daily"
            ) as executor:
                futures = {
                    executor.submit(
                        self._fetch_tencent_daily, symbol, start, end
                    ): symbol
                    for symbol in pending
                }
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        frame = future.result()
                    except Exception as exc:
                        _logger.warning("tencent 日线跳过标的 %s: %s", symbol, exc)
                        continue
                    if isinstance(frame, pd.DataFrame) and not frame.empty:
                        fetched[symbol] = frame

        for symbol, raw_frame in fetched.items():
            df = self._normalize_tencent_df(raw_frame, symbol)
            validated = self._validate_ohlcv(df, symbol)
            self.cache.set_ohlcv(
                symbol,
                validated,
                source=self.name,
                price_mode=adjust or "raw",
                workload=self._cache_workload(),
            )
            out[symbol] = self._annotate_frame(validated)
        if self._cache_workload() == "live_short" and out:
            return out
        if out and len(out) < len(tuple(dict.fromkeys(symbols))):
            missing = [symbol for symbol in symbols if symbol not in out]
            raise DataError(f"{self.name} 日线获取失败（不完整）: 缺少 {missing}")
        require_non_empty_fetch_result(self.name, "日线", symbols, out)
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            if self._endpoint_down():
                # 端点已判死：剩余标的只会逐个撞短路，直接停手，把共享预算留给
                # 竞速同伴与 deferred 兜底源。（本批仍会因覆盖不全而判失败，
                # 这是既有语义，不变。）
                break
            out[symbol] = _normalize_tencent_intraday_volume_to_shares(
                require_fetched_frame(
                    self.name,
                    "分时",
                    symbol,
                    self._fetch_tencent_intraday(symbol, period),
                )
            )
        require_non_empty_fetch_result(self.name, "分时", symbols, out)
        return out

    def fetch_index_intraday(
        self,
        index_codes: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        out = {
            code: require_fetched_frame(
                self.name,
                "指数分时",
                code,
                self._fetch_tencent_intraday(code, period, is_index=True),
            )
            for code in index_codes
        }
        require_non_empty_fetch_result(self.name, "指数分时", index_codes, out)
        return out

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        requested = tuple(
            dict.fromkeys(
                str(symbol).strip() for symbol in symbols if str(symbol).strip()
            )
        )
        if not requested:
            raise DataError("tencent 实时行情未请求标的")
        quotes = self._fetch_tencent_quotes_batch(requested)
        if not quotes:
            raise DataError(f"{self.name} 实时行情获取失败: {symbols}")
        return quotes

    def _fetch_tencent_quotes_batch(self, symbols: tuple[str, ...]) -> dict[str, dict]:
        """Fetch a quote batch in one request and retain partial successes.

        Tencent's quote endpoint accepts comma-separated market-prefixed
        symbols.  The previous one-request-per-symbol loop made a 64-symbol
        live batch spend most of its deadline on throttling and returned only
        the first successful quote under server-side rate limiting.
        """
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                query = ",".join(
                    f"{_get_market_prefix(symbol)}{symbol}" for symbol in symbols
                )
                response = self._session.get(
                    TENCENT_BATCH_QUOTE_URL.format(symbols=query), timeout=10
                )
                quotes = self._parse_tencent_quote_response(response.text)
                return {
                    symbol: quotes[symbol] for symbol in symbols if symbol in quotes
                }
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "tencent 批量实时报价获取失败（重试%d次后放弃）: %s",
                        _MAX_RETRIES,
                        exc,
                    )
        return {}

    @staticmethod
    def _parse_tencent_quote_response(content: str) -> dict[str, dict]:
        quotes: dict[str, dict] = {}
        for match in re.finditer(r'v_(?:sh|sz|bj)(\d{6})="([^"]*)"', content or ""):
            symbol = match.group(1)
            parts = match.group(2).split("~")
            if len(parts) < 50:
                continue
            try:
                price = float(parts[3]) if parts[3] else 0.0
                bid1 = float(parts[9]) if parts[9] else 0.0
                ask1 = float(parts[19]) if parts[19] else 0.0
                volume = float(parts[6]) if parts[6] else 0.0
                amount = float(parts[37]) if parts[37] else 0.0
                limit_up = (
                    float(parts[TENCENT_QUOTE_FIELD_LIMIT_UP])
                    if parts[TENCENT_QUOTE_FIELD_LIMIT_UP]
                    else None
                )
                limit_down = (
                    float(parts[TENCENT_QUOTE_FIELD_LIMIT_DOWN])
                    if parts[TENCENT_QUOTE_FIELD_LIMIT_DOWN]
                    else None
                )
            except (TypeError, ValueError):
                continue
            received_at = now_shanghai().isoformat()
            quotes[symbol] = {
                "name": str(parts[1] or "").strip(),
                "price": price,
                "bid1": bid1,
                "ask1": ask1,
                "volume": volume,
                "amount": amount,
                "limit_up": limit_up,
                "limit_down": limit_down,
                **quote_timestamp_metadata(
                    parse_legacy_quote_timestamp(parts), received_at
                ),
            }
        return quotes

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for code in index_codes:
            cached = self.cache.get_index(
                code,
                start,
                end,
                source=self.name,
                workload=self._cache_workload(),
            )
            if cached is not None and not cached.empty:
                out[code] = self._annotate_frame(cached)
                continue

            df = require_fetched_frame(
                self.name,
                "指数",
                code,
                self._fetch_tencent_daily(code, start, end, is_index=True),
            )
            df = self._normalize_tencent_df(df, code)
            validated = self._validate_ohlcv(df, code)
            self.cache.set_index(
                code,
                validated,
                source=self.name,
                workload=self._cache_workload(),
            )
            out[code] = self._annotate_frame(validated)
        require_non_empty_fetch_result(self.name, "指数", index_codes, out)
        return out

    def _fetch_tencent_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        is_index: bool = False,
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market_symbol = (
                    f"{_get_market_prefix(symbol, is_index=is_index)}{symbol}"
                )
                params = {
                    "param": (
                        f"{market_symbol},day,{start.strftime('%Y-%m-%d')},"
                        f"{end.strftime('%Y-%m-%d')},640"
                        f"{',' if market_symbol.startswith('bj') else ''}"
                    ),
                }
                response = self._session.get(
                    TENCENT_KLINE_URL, params=params, timeout=10
                )
                data = response.json()
                if not data.get("data"):
                    return None
                stock_data = data["data"].get(market_symbol, {})
                if not stock_data:
                    return None
                klines = stock_data.get("day", [])
                if not klines:
                    return None
                rows = []
                for kline in klines:
                    if len(kline) >= 6:
                        rows.append(
                            {
                                "date": kline[0],
                                "open": float(kline[1]),
                                "close": float(kline[2]),
                                "high": float(kline[3]),
                                "low": float(kline[4]),
                                "volume": float(kline[5]),
                            }
                        )
                return pd.DataFrame(rows)
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "tencent 日线获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"tencent 日线获取失败: {symbol}") from exc
        return None

    def _fetch_tencent_intraday_payload(self, market_symbol: str) -> dict:
        """请求并解码分时端点，端点级故障抛 ``TencentEndpointDownError``。"""
        try:
            response = self._session.get(
                TENCENT_INTRADAY_URL,
                params={"code": market_symbol},
                timeout=10,
            )
        except Exception as exc:
            raise TencentEndpointDownError(f"连接失败: {exc}") from exc
        status_code = int(getattr(response, "status_code", 200))
        try:
            payload = response.json()
        except Exception as exc:
            # 非 200 + 非 JSON 就是拦截页（WAF 返回 HTML）。分开报错便于排障。
            raise TencentEndpointDownError(
                f"HTTP {status_code} 且响应不是 JSON（疑似拦截页）"
            ) from exc
        if status_code != 200:
            raise TencentEndpointDownError(f"HTTP {status_code}")
        if not isinstance(payload, dict):
            raise TencentEndpointDownError("响应不是 JSON 对象")
        return payload

    def _fetch_tencent_intraday(
        self, symbol: str, period: str, *, is_index: bool = False
    ) -> pd.DataFrame | None:
        if self._endpoint_down():
            # 冷却期内不发任何请求：让竞速同伴和 deferred 兜底源拿到预算。
            raise DataError(f"tencent 分时端点短路冷却中，跳过 {symbol}")
        market = _get_market_prefix(symbol, is_index=is_index)
        market_symbol = f"{market}{symbol}"
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                payload = self._fetch_tencent_intraday_payload(market_symbol)
            except TencentEndpointDownError as exc:
                tripped = self._record_endpoint_rejection()
                if tripped:
                    raise DataError(
                        f"tencent 分时端点连续拒绝，已短路: {symbol}"
                    ) from exc
                if attempt < _MAX_RETRIES - 1:
                    # 退避同时充当宽限期：瞬时抖动能在几秒内恢复，
                    # 确定性拒绝（WAF/限流）则会在阈值处被短路。
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                    continue
                _logger.warning(
                    "tencent 分时端点故障 %s（重试%d次后放弃）: %s",
                    symbol,
                    _MAX_RETRIES,
                    exc,
                )
                raise DataError(f"tencent 分时获取失败: {symbol}") from exc
            self._record_endpoint_success()
            stock_data = _tencent_intraday_stock_data(payload, market_symbol, symbol)
            if not stock_data:
                return None
            trade_date, minutes = _tencent_intraday_today_minutes(
                stock_data, today=now_shanghai().date()
            )
            rows = _tencent_intraday_rows(trade_date, minutes)
            if not rows:
                return None
            df = pd.DataFrame(rows)
            df["symbol"] = symbol
            df["name"] = symbol
            return df
        return None

    def _fetch_tencent_quote(self, symbol: str) -> dict | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = _get_market_prefix(symbol)
                url = TENCENT_FULL_QUOTE_URL.format(market=market, symbol=symbol)
                response = self._session.get(url, timeout=10)
                parsed = self._parse_tencent_quote_response(response.text)
                return parsed.get(symbol)
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "tencent 实时报价获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"tencent 实时报价获取失败: {symbol}") from exc
        return None

    def _normalize_tencent_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df["symbol"] = symbol
        df["name"] = symbol
        df["amount"] = df["volume"] * df["close"]
        df = apply_limit_suspended_adj(df, symbol, cache=self.cache)
        return df


def _tencent_intraday_stock_data(
    payload: object, market_symbol: str, symbol: str
) -> dict | None:
    """Locate one symbol's node inside a Tencent ``day/query`` payload."""
    node = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(node, dict):
        return None
    value = node.get(market_symbol)
    if not isinstance(value, dict):
        value = node.get(symbol)
    return value if isinstance(value, dict) else None


def _tencent_intraday_today_minutes(
    stock_data: dict, *, today: date
) -> tuple[str, list[str]]:
    """Return ``(trade_date, minute_lines)`` for ``today``'s session only.

    ``day/query`` always answers with several sessions and ignores the ``n``
    parameter, so its first entry is merely the most recent trading day. Only a
    session whose own ``date`` equals ``today`` may be used: relabelling an
    older session with today's date would fabricate intraday freshness, exactly
    what the live_short freshness gate exists to prevent. A missing session for
    ``today`` therefore yields ``("", [])`` and the caller fails closed instead
    of degrading to stale bars.
    """
    sessions = stock_data.get("data")
    if not isinstance(sessions, list):
        return "", []
    wanted = today.strftime("%Y%m%d")
    for session in sessions:
        if not isinstance(session, dict):
            continue
        session_date = str(session.get("date") or "").strip()
        if session_date != wanted:
            continue
        lines = session.get("data")
        if not isinstance(lines, list):
            return session_date, []
        return session_date, [str(line) for line in lines]
    return "", []


def _tencent_intraday_rows(trade_date: str, minutes: list[str]) -> list[dict]:
    """Turn cumulative Tencent minute lines into per-minute OHLCV rows.

    Each line is ``"HHMM price cumulative_volume cumulative_amount"``; volume
    and amount are converted to per-minute deltas. Malformed lines are skipped
    rather than aborting the symbol, so one bad row in a 240-row session cannot
    cost the whole batch — a wholesale format change still yields no rows and
    surfaces as an empty result upstream.
    """
    if not trade_date:
        return []
    day_label = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    rows: list[dict] = []
    previous_price: float | None = None
    previous_volume = 0.0
    previous_amount = 0.0
    for minute in minutes:
        parts = minute.split()
        if len(parts) < 4:
            continue
        try:
            price = float(parts[1])
            cumulative_volume = float(parts[2])
            cumulative_amount = float(parts[3])
        except (TypeError, ValueError):
            continue
        bar_open = price if previous_price is None else previous_price
        rows.append(
            {
                "date": f"{day_label} {parts[0][:2]}:{parts[0][2:]}",
                "open": bar_open,
                "close": price,
                "high": max(bar_open, price),
                "low": min(bar_open, price),
                "volume": max(cumulative_volume - previous_volume, 0.0),
                "amount": max(cumulative_amount - previous_amount, 0.0),
            }
        )
        previous_price = price
        previous_volume = cumulative_volume
        previous_amount = cumulative_amount
    return rows


def _normalize_tencent_intraday_volume_to_shares(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize Tencent minute volume row by row when it is reported in lots."""
    normalized = df.copy()
    if normalized.empty or "volume" not in normalized.columns:
        return normalized
    volume = pd.to_numeric(normalized["volume"], errors="coerce")
    empty = pd.Series(float("nan"), index=normalized.index)
    close = pd.to_numeric(normalized.get("close", empty), errors="coerce")
    amount = pd.to_numeric(normalized.get("amount", empty), errors="coerce")
    valid = (volume > 0) & (close > 0) & (amount > 0)
    implied_unit = amount / (close * volume)
    lots_mask = valid & implied_unit.between(20.0, 200.0)
    normalized["volume"] = volume.where(~lots_mask, volume * 100.0)
    normalized.attrs.update(df.attrs)
    normalized.attrs["volume_unit"] = "shares"
    return normalized
