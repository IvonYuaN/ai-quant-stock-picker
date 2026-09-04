from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Literal
import pandas as pd
import requests

from aqsp.data.source import (
    DataSource,
    OhlcvFrame,
    apply_limit_suspended_adj,
    require_fetched_frame,
    require_fetched_mapping,
    require_non_empty_fetch_result,
)
from aqsp.data.cache import DataCache
from aqsp.core.errors import DataError
from aqsp.core.http import build_http_session, get_http_config
from aqsp.core.time import now_shanghai
from aqsp.data.quote_metadata import parse_vendor_timestamp, quote_timestamp_metadata

_REQUEST_DELAY = 0.3
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_SPOT_PAGE_SIZE = 200
_SPOT_HOSTS = ("push2.eastmoney.com", "push2delay.eastmoney.com")
# 东财真正的分时（分钟线）端点。kline/get?klt=N 在生产环境返回空体/连接重置，
# 只能作为兜底保留；trends2 只提供 1 分钟粒度，更大周期由 _resample_intraday_bars 合成。
# 生产机 IP 会对 push2his 按 IP 限流（连接被无响应重置，curl HTTP=000），而
# push2delay 域名走同一 trends2 路径仍可用（2026-09-03 实测 200 + 当日实时分钟数据，
# 最新一根与服务器时钟同步）。故按域名轮转回退，优先 push2delay。
_TRENDS2_PATH = "/api/qt/stock/trends2/get"
_TRENDS2_HOSTS = ("push2delay.eastmoney.com", "push2his.eastmoney.com")
_TRENDS2_URL = f"https://{_TRENDS2_HOSTS[0]}{_TRENDS2_PATH}"
_TRENDS2_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57"
# A 股午后场次起点；分时重采样需按场次分段，否则午休空档会把 60 分钟桶切歪。
_INTRADAY_PM_SESSION_HOUR = 13

_logger = logging.getLogger("aqsp.data.eastmoney")

# 东财按生产机 IP 限流：trends2 端点密集请求后连接被 WAF 立即重置（curl HTTP=000）。
# 所有 EastmoneySource 实例/线程共享同一请求节奏与熔断状态（限流按 IP 计），
# 用全局节流串行化并发请求 + 熔断快速失败，避免被限流架空盘中新鲜度门。
_REQ_LOCK = threading.Lock()
_LAST_REQ_TS = 0.0
_MIN_REQ_INTERVAL = _REQUEST_DELAY  # 全局最小请求间隔（秒），串行化对东财的并发请求
_BREAKER_CONSEC_FAILURES = 0
_BREAKER_OPEN_UNTIL = 0.0
_BREAKER_THRESHOLD = 4  # 连续限流失败达到此数即熔断
_BREAKER_COOLDOWN = 20.0  # 熔断冷却（秒）


def _eastmoney_is_throttle(exc: BaseException) -> bool:
    """异常是否为东财限流特征（连接被对端无响应重置）。"""
    if isinstance(
        exc, (requests.ConnectionError, ConnectionError, ConnectionAbortedError)
    ):
        return True
    text = str(exc).lower()
    return (
        "remote disconnected" in text
        or "connection aborted" in text
        or "remote end closed connection" in text
    )


def _eastmoney_acquire_request_slot() -> bool:
    """申请一个对东财的请求槽位。

    返回 False 表示熔断器已开启，调用方应直接放弃本次请求（快速失败，
    不再打满单 IP 限额）。否则按全局最小间隔节流后返回 True。
    """
    global _LAST_REQ_TS, _BREAKER_OPEN_UNTIL
    now = time.monotonic()
    with _REQ_LOCK:
        if now < _BREAKER_OPEN_UNTIL:
            return False
        elapsed = now - _LAST_REQ_TS
        if elapsed < _MIN_REQ_INTERVAL:
            time.sleep(_MIN_REQ_INTERVAL - elapsed)
        _LAST_REQ_TS = time.monotonic()
    return True


def _eastmoney_record_result(throttled: bool) -> None:
    """上报一次请求结果，更新熔断计数。

    仅限流特征累加失败并可能在达阈值时开启熔断；成功或普通错误重置计数
    （熔断只应被限流触发，避免把偶发业务错误误判为限流）。
    """
    global _BREAKER_CONSEC_FAILURES, _BREAKER_OPEN_UNTIL
    with _REQ_LOCK:
        if throttled:
            _BREAKER_CONSEC_FAILURES += 1
            if _BREAKER_CONSEC_FAILURES >= _BREAKER_THRESHOLD:
                _BREAKER_OPEN_UNTIL = time.monotonic() + _BREAKER_COOLDOWN
                _logger.warning(
                    "eastmoney 熔断开启：连续 %d 次限流，冷却 %.0fs 后恢复",
                    _BREAKER_CONSEC_FAILURES,
                    _BREAKER_COOLDOWN,
                )
        else:
            _BREAKER_CONSEC_FAILURES = 0
            _BREAKER_OPEN_UNTIL = 0.0  # 成功即证明限流已解除，立即闭合熔断


def _eastmoney_reset_breaker() -> None:
    """测试/运维用：重置熔断与节奏状态。"""
    global _BREAKER_CONSEC_FAILURES, _BREAKER_OPEN_UNTIL, _LAST_REQ_TS
    with _REQ_LOCK:
        _BREAKER_CONSEC_FAILURES = 0
        _BREAKER_OPEN_UNTIL = 0.0
        _LAST_REQ_TS = 0.0


class EastmoneySource(DataSource):
    name: str = "eastmoney"

    def __init__(self, cache: DataCache | None = None) -> None:
        self._session = build_http_session(
            config=get_http_config(),
            headers={
                "Referer": "https://quote.eastmoney.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        self.cache = cache or DataCache()
        self._last_request_ts: float = 0.0
        self._active_workload: str | None = None

    def set_workload(self, workload: str | None) -> None:
        """Set provenance context for the next cache-backed fetch."""
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
        # 改为全局节流（串行化对东财的并发请求），避免触发按 IP 限流。
        _eastmoney_acquire_request_slot()

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
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
                out[symbol] = self._annotate_frame(
                    _normalize_stock_volume_to_shares(cached)
                )
                continue

            try:
                df = require_fetched_frame(
                    self.name,
                    "日线",
                    symbol,
                    self._fetch_eastmoney_daily(symbol, start, end),
                )
            except DataError:
                if self._cache_workload() != "live_short":
                    raise
                _logger.warning("eastmoney 盘中日线跳过无返回标的: %s", symbol)
                continue
            df = self._normalize_eastmoney_df(
                _normalize_stock_volume_to_shares(df), symbol
            )
            validated = self._validate_ohlcv(df, symbol)
            self.cache.set_ohlcv(
                symbol,
                validated,
                source=self.name,
                price_mode=adjust or "raw",
                workload=self._cache_workload(),
            )
            out[symbol] = self._annotate_frame(validated)
        if self._cache_workload() == "live_short":
            if not out:
                raise DataError(f"{self.name} 日线获取失败: {symbols}")
        else:
            require_non_empty_fetch_result(self.name, "日线", symbols, out)
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            out[symbol] = _normalize_stock_volume_to_shares(
                require_fetched_frame(
                    self.name,
                    "分时",
                    symbol,
                    self._fetch_eastmoney_intraday(symbol, period),
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
                self._fetch_eastmoney_intraday(code, period, is_index=True),
            )
            for code in index_codes
        }
        require_non_empty_fetch_result(self.name, "指数分时", index_codes, out)
        return out

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        quotes = {}
        for symbol in symbols:
            quotes[symbol] = require_fetched_mapping(
                self.name,
                "实时行情",
                symbol,
                self._fetch_eastmoney_quote(symbol),
            )
        require_non_empty_fetch_result(self.name, "实时行情", symbols, quotes)
        return quotes

    def get_available_symbols(self) -> list[str]:
        snapshot = self._fetch_eastmoney_spot_snapshot()
        if snapshot.empty or "symbol" not in snapshot.columns:
            raise DataError("eastmoney 全市场快照未返回可用标的")
        return snapshot["symbol"].astype(str).tolist()

    def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
        snapshot = self._fetch_eastmoney_spot_snapshot()
        if snapshot.empty:
            raise DataError("eastmoney 全市场快照为空，无法筛选高流动性标的")
        min_amount_value = max(float(min_amount or 0.0), 0.0)
        row_limit = max(int(limit or 0), 0)
        liquid = snapshot[snapshot["amount"] >= min_amount_value].sort_values(
            "amount",
            ascending=False,
        )
        if row_limit > 0:
            liquid = liquid.head(row_limit)
        return liquid["symbol"].astype(str).tolist()

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
                self._fetch_eastmoney_index(code, start, end),
            )
            df = self._normalize_eastmoney_df(df, code)
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

    def _fetch_eastmoney_daily(
        self, symbol: str, start: date, end: date
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "1" if symbol.startswith("6") else "0"
                url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                params = {
                    "secid": f"{market}.{symbol}",
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": "101",
                    "fqt": "0",
                    "beg": start.strftime("%Y%m%d"),
                    "end": end.strftime("%Y%m%d"),
                }
                response = self._session.get(url, params=params, timeout=10)
                data = response.json()
                if not data.get("data"):
                    return None
                payload = data["data"]
                klines = payload.get("klines", [])
                if not klines:
                    return None
                rows = []
                for kline in klines:
                    parts = kline.split(",")
                    if len(parts) >= 11:
                        rows.append(
                            {
                                "date": parts[0],
                                "open": float(parts[1]),
                                "close": float(parts[2]),
                                "high": float(parts[3]),
                                "low": float(parts[4]),
                                "volume": float(parts[5]),
                                # Eastmoney 日线 kline:
                                # 0日期 1开 2收 3高 4低 5成交量 6成交额 7振幅 8涨跌幅 9涨跌额 10换手率
                                # 这里必须取 parts[6]，之前误取 parts[9]（涨跌额），
                                # 会把大票真实成交额误判成几十/几百，触发流动性错杀。
                                "amount": float(parts[6]),
                            }
                        )
                frame = pd.DataFrame(rows)
                frame["name"] = str(payload.get("name", "") or symbol)
                return frame
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney 日线获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"eastmoney 日线获取失败: {symbol}") from exc
        return None

    def _fetch_eastmoney_intraday(
        self, symbol: str, period: str, *, is_index: bool = False
    ) -> pd.DataFrame | None:
        """抓取东财当日分时。

        主路径走 trends2（1 分钟粒度 + 本地重采样）；trends2 空返回时
        回落到旧的 kline/get?klt=N，避免单点端点抖动直接判死。
        """
        frame = self._fetch_eastmoney_intraday_trends2(
            symbol, period, is_index=is_index
        )
        if frame is not None and not frame.empty:
            return frame
        return self._fetch_eastmoney_intraday_kline(symbol, period, is_index=is_index)

    def _fetch_eastmoney_intraday_trends2(
        self, symbol: str, period: str, *, is_index: bool = False
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            if not _eastmoney_acquire_request_slot():
                _logger.warning("eastmoney 熔断中，跳过 %s trends2 分时请求", symbol)
                return None
            try:
                market = "1" if is_index or symbol.startswith("6") else "0"
                # 与 _SPOT_HOSTS 同样按重试轮转域名：优先 push2delay（生产机实测可用），
                # 失败则回退 push2his，避免单域名被 IP 限流即判死整个东财兜底。
                host = _TRENDS2_HOSTS[attempt % len(_TRENDS2_HOSTS)]
                response = self._session.get(
                    f"https://{host}{_TRENDS2_PATH}",
                    params={
                        "secid": f"{market}.{symbol}",
                        "ut": "7eea3edcaed734bea9cbfc24409ed989",
                        "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": _TRENDS2_FIELDS2,
                        "iscr": "0",
                        "ndays": "1",
                    },
                    timeout=10,
                )
                payload = (response.json() or {}).get("data") or {}
                frame = _parse_eastmoney_trends2(payload.get("trends") or [])
                if frame.empty:
                    return None
                frame = _resample_intraday_bars(frame, period)
                if frame.empty:
                    return None
                frame["symbol"] = symbol
                frame["name"] = str(payload.get("name") or symbol)
                _eastmoney_record_result(False)
                return frame
            except Exception as exc:
                throttled = _eastmoney_is_throttle(exc)
                _eastmoney_record_result(throttled)
                if attempt < _MAX_RETRIES - 1:
                    if throttled:
                        # 限流时不再紧凑重试，交给熔断冷却避免打满单 IP 限额
                        return None
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney trends2 分时获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    return None
        return None

    def _fetch_eastmoney_intraday_kline(
        self, symbol: str, period: str, *, is_index: bool = False
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            if not _eastmoney_acquire_request_slot():
                _logger.warning("eastmoney 熔断中，跳过 %s 分时(kline)请求", symbol)
                return None
            try:
                market = "1" if is_index or symbol.startswith("6") else "0"
                klt_map = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60"}
                url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                params = {
                    "secid": f"{market}.{symbol}",
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": klt_map.get(period, "5"),
                    "fqt": "0",
                }
                response = self._session.get(url, params=params, timeout=10)
                data = response.json()
                if not data.get("data"):
                    return None
                payload = data["data"]
                klines = payload.get("klines", [])
                if not klines:
                    return None
                rows = []
                for kline in klines:
                    parts = kline.split(",")
                    if len(parts) >= 11:
                        rows.append(
                            {
                                "date": parts[0],
                                "open": float(parts[1]),
                                "close": float(parts[2]),
                                "high": float(parts[3]),
                                "low": float(parts[4]),
                                "volume": float(parts[5]),
                                "amount": float(parts[6]),
                            }
                        )
                df = pd.DataFrame(rows)
                df["symbol"] = symbol
                df["name"] = symbol
                _eastmoney_record_result(False)
                return df
            except Exception as exc:
                throttled = _eastmoney_is_throttle(exc)
                _eastmoney_record_result(throttled)
                if attempt < _MAX_RETRIES - 1:
                    if throttled:
                        # 限流时不再紧凑重试，交给熔断冷却避免打满单 IP 限额
                        return None
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney 分时获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"eastmoney 分时获取失败: {symbol}") from exc
        return None

    def _fetch_eastmoney_quote(self, symbol: str) -> dict | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "1" if symbol.startswith("6") else "0"
                url = "https://push2.eastmoney.com/api/qt/stock/get"
                params = {
                    "secid": f"{market}.{symbol}",
                    "fields": "f57,f58,f10,f60,f61,f152,f168,f177",
                }
                response = self._session.get(url, params=params, timeout=10)
                data = response.json()
                if not data.get("data"):
                    return None
                d = data["data"]
                received_at = now_shanghai().isoformat()
                vendor_ts = parse_vendor_timestamp(d.get("f86") or d.get("f124"))
                return {
                    "price": float(d.get("f60", 0)),
                    "bid1": float(d.get("f152", 0)),
                    "ask1": float(d.get("f168", 0)),
                    "volume": float(d.get("f61", 0)),
                    "amount": float(d.get("f177", 0)),
                    **quote_timestamp_metadata(vendor_ts, received_at),
                }
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney 实时报价获取失败 %s（重试%d次后放弃）: %s",
                        symbol,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"eastmoney 实时报价获取失败: {symbol}") from exc
        return None

    def _fetch_eastmoney_spot_snapshot(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        page = 1
        received_rows = 0
        expected_rows: int | None = None
        while True:
            data = self._fetch_eastmoney_spot_page(page)
            payload = data.get("data") or {}
            diff = payload.get("diff") or []
            if expected_rows is None:
                raw_total = payload.get("total")
                try:
                    parsed_total = int(raw_total)
                except (TypeError, ValueError):
                    parsed_total = 0
                if parsed_total > 0:
                    expected_rows = parsed_total
            if not diff:
                if expected_rows is not None and received_rows < expected_rows:
                    raise DataError(
                        "eastmoney 全市场快照分页不完整: "
                        f"received={received_rows}, expected={expected_rows}"
                    )
                break
            received_rows += len(diff)
            for item in diff:
                symbol = str(item.get("f12") or "").strip()
                name = str(item.get("f14") or "").strip()
                if len(symbol) != 6 or not symbol.isdigit():
                    continue
                if name.startswith(("ST", "*ST", "退市")):
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "name": name or symbol,
                        "price": _safe_float(item.get("f2")),
                        "volume": _safe_float(item.get("f5")),
                        "amount": _safe_float(item.get("f6")),
                    }
                )
            if expected_rows is not None:
                if received_rows >= expected_rows:
                    break
            elif len(diff) < _SPOT_PAGE_SIZE:
                break
            page += 1
        if not rows:
            raise DataError("eastmoney 全市场快照无有效 A 股标的")
        return pd.DataFrame(rows)

    def _fetch_eastmoney_spot_page(self, page: int) -> dict:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                # Eastmoney occasionally resets connections on a page-specific
                # basis. The delay host serves the same live snapshot contract.
                host = _SPOT_HOSTS[attempt % len(_SPOT_HOSTS)]
                url = f"https://{host}/api/qt/clist/get"
                params = {
                    "pn": page,
                    "pz": _SPOT_PAGE_SIZE,
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f6",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                    "fields": "f2,f5,f6,f12,f14",
                }
                response = self._session.get(url, params=params, timeout=10)
                return response.json()
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney 全市场快照获取失败 page=%s（重试%d次后放弃）: %s",
                        page,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError("eastmoney 全市场快照获取失败") from exc
        return {}

    def _fetch_eastmoney_index(
        self, code: str, start: date, end: date
    ) -> pd.DataFrame | None:
        for attempt in range(_MAX_RETRIES):
            try:
                self._throttle()
                market = "1" if code.startswith("000") else "0"
                url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                params = {
                    "secid": f"{market}.{code}",
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": "101",
                    "fqt": "0",
                    "beg": start.strftime("%Y%m%d"),
                    "end": end.strftime("%Y%m%d"),
                }
                response = self._session.get(url, params=params, timeout=10)
                data = response.json()
                if not data.get("data"):
                    return None
                payload = data["data"]
                klines = payload.get("klines", [])
                if not klines:
                    return None
                rows = []
                for kline in klines:
                    parts = kline.split(",")
                    if len(parts) >= 11:
                        rows.append(
                            {
                                "date": parts[0],
                                "open": float(parts[1]),
                                "close": float(parts[2]),
                                "high": float(parts[3]),
                                "low": float(parts[4]),
                                "volume": float(parts[5]),
                                "amount": float(parts[6]),
                            }
                        )
                frame = pd.DataFrame(rows)
                frame["name"] = str(payload.get("name", "") or code)
                return frame
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** (attempt + 1))
                else:
                    _logger.warning(
                        "eastmoney 指数获取失败 %s（重试%d次后放弃）: %s",
                        code,
                        _MAX_RETRIES,
                        exc,
                    )
                    raise DataError(f"eastmoney 指数获取失败: {code}") from exc
        return None

    def _normalize_eastmoney_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df["symbol"] = symbol
        if "name" not in df.columns:
            df["name"] = symbol
        else:
            df["name"] = df["name"].astype(str).replace("", symbol)
        df = apply_limit_suspended_adj(df, symbol, cache=self.cache)
        return df


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_eastmoney_trends2(trends: list[object]) -> pd.DataFrame:
    """Parse eastmoney trends2 rows.

    行格式 ``"YYYY-MM-DD HH:MM,open,close,high,low,volume,amount"``；
    volume 单位为手，由 ``_normalize_stock_volume_to_shares`` 统一换算成股。
    """
    rows: list[dict[str, object]] = []
    for item in trends or []:
        parts = str(item).split(",")
        if len(parts) < 7:
            continue
        try:
            rows.append(
                {
                    "date": parts[0].strip(),
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "amount": float(parts[6]),
                }
            )
        except ValueError:
            continue
    return pd.DataFrame(
        rows,
        columns=[
            "date",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
        ],
    )


def _resample_intraday_bars(frame: pd.DataFrame, period: object) -> pd.DataFrame:
    """把 1 分钟分时合成 period 分钟粒度。

    A 股分早晚两场（09:30-11:30 / 13:00-15:00）。直接对整日 resample 会让午休
    空档把 60 分钟桶切歪（13:00-13:29 会被塞进 12:30 桶），所以按场次分段、
    各以本场首根 K 为 origin 重采样，再按时间顺序拼接。
    """
    try:
        minutes = int(period)
    except (TypeError, ValueError):
        return frame
    if minutes <= 1 or frame.empty or "date" not in frame.columns:
        return frame
    stamps = pd.to_datetime(frame["date"], errors="coerce")
    if stamps.isna().any():
        # 时间列不可信时宁可返回 1 分钟原始数据，也不产出错误聚合。
        return frame
    work = frame.copy()
    work["_ts"] = stamps
    work["_session"] = (stamps.dt.hour >= _INTRADAY_PM_SESSION_HOUR).astype(int)
    chunks: list[pd.DataFrame] = []
    for _, block in work.groupby([stamps.dt.date, work["_session"]], sort=True):
        block = block.sort_values("_ts")
        resampled = (
            block.set_index("_ts")
            .resample(f"{minutes}min", origin=block["_ts"].iloc[0])
            .agg(
                {
                    "open": "first",
                    "close": "last",
                    "high": "max",
                    "low": "min",
                    "volume": "sum",
                    "amount": "sum",
                }
            )
            .dropna(subset=["open"])
        )
        if resampled.empty:
            continue
        chunks.append(resampled.reset_index())
    if not chunks:
        return frame.iloc[0:0]
    merged = pd.concat(chunks, ignore_index=True)
    merged = merged.rename(columns={"_ts": "date"})
    merged["date"] = pd.to_datetime(merged["date"]).dt.strftime("%Y-%m-%d %H:%M")
    return merged[
        ["date", "open", "close", "high", "low", "volume", "amount"]
    ].reset_index(drop=True)


def _normalize_stock_volume_to_shares(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize Eastmoney stock volume from lots to the project-wide share unit."""
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
