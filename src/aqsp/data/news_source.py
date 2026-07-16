from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import os
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
import threading
from time import monotonic
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yaml

from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai

_RSS_CORE_TRIGGER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "commercial_space": ("spacex", "starlink", "commercial space", "商业航天"),
    "physical_ai": ("physical ai", "nvidia", "robotics", "具身智能"),
    "us_risk_on": ("nasdaq", "s&p 500", "risk-on", "美股", "风险偏好"),
    "geopolitics": ("war", "geopolitical", "gold", "defense", "战争", "军工"),
    "oil_price_shock": ("oil", "crude", "brent", "opec", "原油", "油价"),
}
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_DIRECT_STOCK_NEWS_TIMEOUT_SECONDS = 8.0
_DEFAULT_NEWS_SOURCE_TIMEOUT_SECONDS = 8.0
_COMPOSITE_TIMEOUT_MARGIN_SECONDS = 0.25
_RSS_FRAME_CACHE_TTL_SECONDS = 15.0

NewsSourceStatus = Literal["ok", "empty", "timeout", "partial", "failed"]
NewsSourceRegion = Literal["domestic", "international", "mixed"]


def _source_fetched_at() -> str:
    return now_shanghai().isoformat(timespec="seconds")


@dataclass(frozen=True)
class NewsSourceHealth:
    """One source attempt, kept separate from the news rows it produced."""

    name: str
    region: NewsSourceRegion
    status: NewsSourceStatus
    attempted: int = 1
    successful: int = 0
    row_count: int = 0
    fetched_at: str = ""
    warnings: tuple[str, ...] = ()


class _AkshareOptionalDependencyError(RuntimeError):
    """Raised only when the optional AkShare package is unavailable."""


class NewsSource(Protocol):
    name: str

    def fetch_symbol_news(self, symbol: str) -> list[pd.DataFrame]: ...

    def fetch_global_news(self) -> list[pd.DataFrame]: ...


@dataclass(frozen=True)
class RssFeedConfig:
    name: str
    url: str
    category: str = ""
    enabled: bool = True
    symbols: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    max_items: int = 20
    region: NewsSourceRegion = "domestic"


@dataclass(frozen=True)
class RssNewsRuntimeSummary:
    enabled: bool
    feed_count: int
    feed_names: tuple[str, ...]
    covered_triggers: tuple[str, ...]
    missing_triggers: tuple[str, ...]
    keyword_gated_feeds: int

    @property
    def all_core_triggers_covered(self) -> bool:
        return self.enabled and self.feed_count > 0 and not self.missing_triggers


def _configured_news_source_timeout_seconds() -> float | None:
    raw = str(os.getenv("AQSP_NEWS_SOURCE_TIMEOUT_SECONDS", "") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _composite_timeout_seconds(sources: Sequence[NewsSource]) -> float:
    configured = _configured_news_source_timeout_seconds()
    budgets = [
        float(getattr(source, "_timeout_seconds", 0.0) or 0.0)
        for source in sources
        if float(getattr(source, "_timeout_seconds", 0.0) or 0.0) > 0
    ]
    if configured is not None:
        budgets.append(configured)
    budget = min(budgets) if budgets else _DEFAULT_NEWS_SOURCE_TIMEOUT_SECONDS
    return max(0.1, budget - _COMPOSITE_TIMEOUT_MARGIN_SECONDS)


def _run_callable_with_timeout(fetch: Callable[[], object], timeout_seconds: float) -> object:
    """Run an optional third-party call without letting it block the news chain."""

    result: dict[str, object] = {}
    lock = threading.Lock()

    def run() -> None:
        try:
            value = fetch()
        except BaseException as exc:
            with lock:
                result["error"] = exc
        else:
            with lock:
                result["value"] = value

    worker = threading.Thread(target=run, name="aqsp-news-endpoint", daemon=True)
    worker.start()
    worker.join(max(0.0, float(timeout_seconds)))
    with lock:
        if "error" in result:
            raise result["error"]  # type: ignore[misc]
        if "value" in result:
            return result["value"]
    raise TimeoutError(f"新闻端点超过 {float(timeout_seconds):.1f}s 未返回")


class CompositeNewsSource:
    name = "composite_news"
    region: NewsSourceRegion = "mixed"

    def __init__(
        self,
        sources: tuple[NewsSource, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        self._sources = sources
        self._timeout_seconds = (
            _composite_timeout_seconds(sources)
            if timeout_seconds is None
            else max(0.1, float(timeout_seconds) - _COMPOSITE_TIMEOUT_MARGIN_SECONDS)
        )
        self._last_health: tuple[NewsSourceHealth, ...] = ()

    @property
    def last_health(self) -> tuple[NewsSourceHealth, ...]:
        return self._last_health

    def fetch_symbol_news(self, symbol: str) -> list[pd.DataFrame]:
        frames, errors, health = _collect_composite_news(
            self._sources,
            lambda source: source.fetch_symbol_news(symbol),
            timeout_seconds=self._timeout_seconds,
        )
        self._last_health = tuple(health)
        if frames:
            _attach_source_warnings(frames, errors)
            _attach_source_health(frames, health)
            return frames
        if errors:
            raise DataError(f"组合个股新闻获取失败: {symbol}; {'; '.join(errors)}")
        raise DataError(f"组合个股新闻无结果且无来源状态: {symbol}")

    def fetch_global_news(self) -> list[pd.DataFrame]:
        frames, errors, health = _collect_composite_news(
            self._sources,
            lambda source: source.fetch_global_news(),
            timeout_seconds=self._timeout_seconds,
        )
        self._last_health = tuple(health)
        if frames:
            _attach_source_warnings(frames, errors)
            _attach_source_health(frames, health)
            return frames
        if errors:
            raise DataError(f"组合全市场新闻获取失败: {'; '.join(errors)}")
        raise DataError("组合全市场新闻无结果且无来源状态")


def _collect_composite_news(
    sources: Sequence[NewsSource],
    fetch: Callable[[NewsSource], list[pd.DataFrame]],
    *,
    timeout_seconds: float | None = None,
) -> tuple[list[pd.DataFrame], list[str], list[NewsSourceHealth]]:
    """Collect source results concurrently and retain partial success.

    Composite news used to call RSS and AkShare serially.  A slow optional
    source could consume the shared deadline after RSS had already succeeded,
    causing the caller to lose valid international or domestic evidence.
    """

    results: dict[int, tuple[list[pd.DataFrame], BaseException | None]] = {}
    results_lock = threading.Lock()
    # The composite deadline is owned by CompositeNewsSource.  This helper is
    # also kept usable in isolation for tests and small adapters.
    timeout_seconds = max(
        0.1,
        float(
            timeout_seconds
            if timeout_seconds is not None
            else _composite_timeout_seconds(sources)
        ),
    )

    def fetch_one(index: int, source: NewsSource) -> None:
        try:
            source_frames = fetch(source)
        except BaseException as exc:
            source_frames = []
            error: BaseException | None = exc
        else:
            error = None
        with results_lock:
            results[index] = (source_frames, error)

    workers = [
        threading.Thread(
            target=fetch_one,
            args=(index, source),
            name=f"aqsp-composite-news-{index}",
            daemon=True,
        )
        for index, source in enumerate(sources)
    ]
    for worker in workers:
        worker.start()
    deadline = monotonic() + timeout_seconds
    for worker in workers:
        worker.join(max(0.0, deadline - monotonic()))

    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    health: list[NewsSourceHealth] = []
    for index, source in enumerate(sources):
        if index not in results:
            source_frames: list[pd.DataFrame] = []
            error: BaseException | None = TimeoutError(
                f"{source.name} 超过 {timeout_seconds:.1f}s 未返回"
            )
        else:
            source_frames, error = results[index]
        source_name = str(getattr(source, "name", "unknown") or "unknown")
        if error is not None:
            warning = f"{source_name}: {error}"
            errors.append(warning)
            health.append(
                NewsSourceHealth(
                    name=source_name,
                    region=_normalize_region(getattr(source, "region", "mixed")),
                    status=_status_from_exception(error),
                    fetched_at=_source_fetched_at(),
                    warnings=(warning,),
                )
            )
            continue
        source_health = tuple(getattr(source, "last_health", ()))
        if source_frames:
            frames.extend(source_frames)
            if source_health:
                health.extend(source_health)
            else:
                health.append(
                    NewsSourceHealth(
                        name=source_name,
                        region=_normalize_region(getattr(source, "region", "mixed")),
                        status="ok",
                        successful=1,
                        row_count=sum(len(frame) for frame in source_frames),
                        fetched_at=_source_fetched_at(),
                    )
                )
        else:
            warning = f"{source_name}: empty"
            errors.append(warning)
            health.append(
                NewsSourceHealth(
                    name=source_name,
                    region=_normalize_region(getattr(source, "region", "mixed")),
                    status="empty",
                    fetched_at=_source_fetched_at(),
                    warnings=(warning,),
                )
            )
    return frames, errors, health


class RssNewsSource:
    name = "rss_news"
    region: NewsSourceRegion = "mixed"

    def __init__(
        self,
        feeds: tuple[RssFeedConfig, ...],
        *,
        timeout_seconds: float = 6.0,
        max_concurrency: int = 4,
    ) -> None:
        self._feeds = tuple(feed for feed in feeds if feed.enabled and feed.url)
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._max_concurrency = max(1, int(max_concurrency))
        self._last_health: tuple[NewsSourceHealth, ...] = ()
        self._frames_condition = threading.Condition()
        self._frames_loading = False
        self._frames_cache: tuple[pd.DataFrame, ...] = ()
        self._frames_cache_at = 0.0

    def fetch_symbol_news(self, symbol: str) -> list[pd.DataFrame]:
        clean_symbol = str(symbol or "").strip()
        if not clean_symbol:
            raise DataError("RSS 个股新闻缺少股票代码")
        frames = self._fetch_frames()
        filtered: list[pd.DataFrame] = []
        for frame in frames:
            if frame.empty:
                continue
            mask = frame.apply(
                lambda row: _row_matches_symbol(row, clean_symbol),
                axis=1,
            )
            matched = frame[mask].copy()
            if not matched.empty:
                filtered.append(matched)
        return filtered

    def fetch_global_news(self) -> list[pd.DataFrame]:
        return self._fetch_frames()

    @property
    def last_health(self) -> tuple[NewsSourceHealth, ...]:
        return self._last_health

    def _fetch_frames(self) -> list[pd.DataFrame]:
        now = monotonic()
        with self._frames_condition:
            if self._frames_cache and now - self._frames_cache_at <= _RSS_FRAME_CACHE_TTL_SECONDS:
                return list(self._frames_cache)
            if self._frames_loading:
                remaining = max(0.1, self._timeout_seconds)
                self._frames_condition.wait(timeout=remaining)
                if self._frames_cache and monotonic() - self._frames_cache_at <= _RSS_FRAME_CACHE_TTL_SECONDS:
                    return list(self._frames_cache)
                raise TimeoutError(f"RSS 新闻共享抓取超过 {self._timeout_seconds:.1f}s 未返回")
            self._frames_loading = True
        try:
            frames = self._fetch_frames_uncached()
        except BaseException:
            with self._frames_condition:
                self._frames_loading = False
                self._frames_condition.notify_all()
            raise
        with self._frames_condition:
            self._frames_cache = tuple(frames)
            self._frames_cache_at = monotonic()
            self._frames_loading = False
            self._frames_condition.notify_all()
        return frames

    def _fetch_frames_uncached(self) -> list[pd.DataFrame]:
        frames: list[pd.DataFrame] = []
        errors: list[str] = []
        health: list[NewsSourceHealth] = []
        if not self._feeds:
            fetched_at = _source_fetched_at()
            self._last_health = (
                NewsSourceHealth(
                    name=self.name,
                    region=self.region,
                    status="failed",
                    fetched_at=fetched_at,
                    warnings=("未配置 RSS feed",),
                ),
            )
            raise DataError("RSS 新闻未配置可用 feed")

        results: dict[int, tuple[pd.DataFrame | None, BaseException | None]] = {}
        results_lock = threading.Lock()
        semaphore = threading.BoundedSemaphore(self._max_concurrency)

        def fetch_one(index: int, feed: RssFeedConfig) -> None:
            with semaphore:
                try:
                    frame = self._fetch_feed(feed)
                except BaseException as exc:
                    frame = None
                    error: BaseException | None = exc
                else:
                    error = None
                with results_lock:
                    results[index] = (frame, error)

        workers = [
            threading.Thread(
                target=fetch_one,
                args=(index, feed),
                name=f"aqsp-rss-{index}",
                daemon=True,
            )
            for index, feed in enumerate(self._feeds)
        ]
        for worker in workers:
            worker.start()
        deadline = monotonic() + self._timeout_seconds
        for worker in workers:
            worker.join(max(0.0, deadline - monotonic()))

        for index, feed in enumerate(self._feeds):
            if index not in results:
                frame = None
                error: BaseException | None = TimeoutError(
                    f"feed 超过 {self._timeout_seconds:.1f}s 未返回"
                )
            else:
                frame, error = results[index]

            if error is not None:
                warning = f"{feed.name}: {error}"
                errors.append(warning)
                health.append(
                    NewsSourceHealth(
                        name=feed.name,
                        region=feed.region,
                        status=_status_from_exception(error),
                        fetched_at=_source_fetched_at(),
                        warnings=(warning,),
                    )
                )
                continue
            if frame is not None and not frame.empty:
                frame.attrs["aqsp_fetched_at"] = _source_fetched_at()
                frames.append(frame)
                health.append(
                    NewsSourceHealth(
                        name=feed.name,
                        region=feed.region,
                        status="ok",
                        successful=1,
                        row_count=len(frame),
                        fetched_at=str(frame.attrs["aqsp_fetched_at"]),
                    )
                )
            else:
                warning = f"{feed.name}: empty"
                errors.append(warning)
                health.append(
                    NewsSourceHealth(
                        name=feed.name,
                        region=feed.region,
                        status="empty",
                        fetched_at=_source_fetched_at(),
                        warnings=(warning,),
                    )
                )
        self._last_health = tuple(health)
        if frames and errors:
            _attach_source_warnings(frames, errors)
        _attach_source_health(frames, health)
        if not frames and errors:
            raise DataError(f"RSS 新闻获取失败: {'; '.join(errors)}")
        return frames

    def _fetch_feed(self, feed: RssFeedConfig) -> pd.DataFrame:
        response = requests.get(
            feed.url,
            headers={"User-Agent": "AQSP/0.1 news radar"},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        return _parse_rss_xml(
            response.content,
            source_name=feed.name,
            category=feed.category,
            region=feed.region,
            symbols=feed.symbols,
            keywords=feed.keywords,
            max_items=feed.max_items,
        )


class AkshareNewsSource:
    name = "akshare_news"
    region: NewsSourceRegion = "mixed"

    def __init__(
        self,
        *,
        timeout_seconds: float = _DEFAULT_NEWS_SOURCE_TIMEOUT_SECONDS,
    ) -> None:
        try:
            import akshare as ak
        except ImportError as exc:
            raise _AkshareOptionalDependencyError("akshare not installed") from exc
        self._ak = ak
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._last_health: tuple[NewsSourceHealth, ...] = ()

    @property
    def last_health(self) -> tuple[NewsSourceHealth, ...]:
        return self._last_health

    def fetch_symbol_news(self, symbol: str) -> list[pd.DataFrame]:
        frames, errors = self._collect_frames(
            (
                ("stock_news_em", lambda: self._fetch_stock_news_em(symbol)),
                (
                    "stock_individual_notice_report",
                    lambda: self._fetch_individual_notice_report(symbol),
                ),
                (
                    "stock_research_report_em",
                    lambda: self._ak.stock_research_report_em(symbol=symbol),
                ),
            )
        )
        if not frames:
            raise DataError(f"akshare 个股新闻获取失败: {symbol}; {'; '.join(errors)}")
        return frames

    def _fetch_stock_news_em(self, symbol: str) -> object:
        """Use AkShare first, then bypass its known JSONP parser breakage."""
        try:
            return self._ak.stock_news_em(symbol=symbol)
        except Exception as exc:
            message = str(exc).casefold()
            if (
                "invalid regular expression" not in message
                and "invalid escape sequence" not in message
            ):
                raise
            return _fetch_eastmoney_stock_news_compat(symbol)

    def _fetch_individual_notice_report(self, symbol: str) -> object:
        """AkShare 1.18+ requires ``security``; retain older adapter fallback."""
        fetcher = self._ak.stock_individual_notice_report
        try:
            return fetcher(security=symbol)
        except TypeError as exc:
            if "security" not in str(exc) and "unexpected keyword" not in str(exc):
                raise
            return fetcher(symbol=symbol)

    def fetch_global_news(self) -> list[pd.DataFrame]:
        frames, errors = self._collect_frames(
            (
                ("stock_info_global_cls", self._ak.stock_info_global_cls),
                ("stock_info_global_em", self._ak.stock_info_global_em),
                ("stock_info_global_ths", self._ak.stock_info_global_ths),
                ("stock_info_global_futu", self._ak.stock_info_global_futu),
                ("stock_info_global_sina", self._ak.stock_info_global_sina),
                ("news_cctv", self._ak.news_cctv),
                ("news_economic_baidu", self._ak.news_economic_baidu),
                ("stock_notice_report", self._ak.stock_notice_report),
            )
        )
        if not frames:
            raise DataError(f"akshare 全市场新闻获取失败: {'; '.join(errors)}")
        return frames

    def _collect_frames(
        self,
        fetchers: tuple[tuple[str, Callable[[], object]], ...],
    ) -> tuple[list[pd.DataFrame], list[str]]:
        frames: list[pd.DataFrame] = []
        errors: list[str] = []
        health: list[NewsSourceHealth] = []
        deadline = monotonic() + max(
            0.1,
            float(
                getattr(
                    self,
                    "_timeout_seconds",
                    _DEFAULT_NEWS_SOURCE_TIMEOUT_SECONDS,
                )
            ),
        )
        for name, fetch in fetchers:
            remaining = deadline - monotonic()
            if remaining <= 0:
                error: BaseException = TimeoutError("akshare 批次超过截止时间")
                warning = f"{name}: {error}"
                errors.append(warning)
                health.append(
                    NewsSourceHealth(
                        name=name,
                        region=_region_for_source_name(name),
                        status="timeout",
                        fetched_at=_source_fetched_at(),
                        warnings=(warning,),
                    )
                )
                break
            try:
                frame = _run_callable_with_timeout(fetch, remaining)
            except TimeoutError as exc:
                # The caller's bounded timeout is fail-fast for this batch. Continuing
                # through more AkShare endpoints only multiplies a blocked network call.
                warning = f"{name}: {exc}"
                errors.append(warning)
                health.append(
                    NewsSourceHealth(
                        name=name,
                        region=_region_for_source_name(name),
                        status="timeout",
                        fetched_at=_source_fetched_at(),
                        warnings=(warning,),
                    )
                )
                break
            except Exception as exc:
                warning = f"{name}: {exc}"
                errors.append(warning)
                health.append(
                    NewsSourceHealth(
                        name=name,
                        region=_region_for_source_name(name),
                        status=_status_from_exception(exc),
                        fetched_at=_source_fetched_at(),
                        warnings=(warning,),
                    )
                )
                continue
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                frame.attrs["aqsp_fetched_at"] = _source_fetched_at()
                frames.append(frame)
                health.append(
                    NewsSourceHealth(
                        name=name,
                        region=_region_for_source_name(name),
                        status="ok",
                        successful=1,
                        row_count=len(frame),
                        fetched_at=str(frame.attrs["aqsp_fetched_at"]),
                    )
                )
            else:
                warning = f"{name}: empty"
                errors.append(warning)
                health.append(
                    NewsSourceHealth(
                        name=name,
                        region=_region_for_source_name(name),
                        status="empty",
                        fetched_at=_source_fetched_at(),
                        warnings=(warning,),
                    )
                )
        self._last_health = tuple(health)
        if frames and errors:
            warnings = tuple(errors[:5])
            for frame in frames:
                frame.attrs["aqsp_warnings"] = warnings
        _attach_source_health(frames, health)
        return frames, errors


def _fetch_eastmoney_stock_news_compat(symbol: str) -> pd.DataFrame:
    """Fetch Eastmoney stock news without AkShare's stale JSONP stripper."""
    inner_param = {
        "uid": "",
        "keyword": str(symbol),
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": 10,
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    response = requests.get(
        "https://search-api-web.eastmoney.com/search/jsonp",
        params={
            "cb": "aqsp_callback",
            "param": json.dumps(inner_param, ensure_ascii=False, separators=(",", ":")),
            "_": str(int(now_shanghai().timestamp() * 1000)),
        },
        headers={
            "Accept": "*/*",
            "Referer": f"https://so.eastmoney.com/news/s?keyword={symbol}",
            "User-Agent": "AQSP/0.1 news radar",
        },
        timeout=_DIRECT_STOCK_NEWS_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    text = str(getattr(response, "text", "") or "").strip()
    if not text:
        raise DataError(f"eastmoney 个股新闻返回空响应: {symbol}")
    if "(" in text:
        text = text.split("(", 1)[1]
        if text.endswith(");"):
            text = text[:-2]
        elif text.endswith(")"):
            text = text[:-1]
    payload = json.loads(text)
    rows = (payload.get("result") or {}).get("cmsArticleWebOld") or []
    if not isinstance(rows, list) or not rows:
        raise DataError(f"eastmoney 个股新闻无结果: {symbol}")
    frame = pd.DataFrame(rows)
    frame["新闻标题"] = frame.get("title", "")
    frame["新闻内容"] = frame.get("content", "")
    frame["发布时间"] = frame.get("date", "")
    frame["文章来源"] = frame.get("mediaName", "")
    codes = (
        frame["code"] if "code" in frame.columns else pd.Series("", index=frame.index)
    )
    frame["新闻链接"] = [
        f"https://finance.eastmoney.com/a/{code}.html" if code else ""
        for code in codes.fillna("").astype(str)
    ]
    frame["关键词"] = str(symbol)
    frame.attrs["aqsp_fetched_at"] = _source_fetched_at()
    return frame


def build_default_news_source() -> NewsSource:
    rss_source = build_rss_news_source_from_config()
    try:
        configured_timeout = _configured_news_source_timeout_seconds()
        if configured_timeout is None:
            akshare_source: NewsSource | None = AkshareNewsSource()
        else:
            akshare_source = AkshareNewsSource(timeout_seconds=configured_timeout)
    except _AkshareOptionalDependencyError:
        akshare_source = None

    sources: list[NewsSource] = []
    if rss_source is not None:
        sources.append(rss_source)
    if akshare_source is not None:
        sources.append(akshare_source)
    if not sources:
        raise DataError("未配置可用新闻源: akshare 未安装且 RSS 未启用或无有效订阅源")
    if len(sources) == 1:
        return sources[0]
    return CompositeNewsSource(
        tuple(sources),
        timeout_seconds=_configured_news_source_timeout_seconds(),
    )


def build_rss_news_source_from_config(path: str | None = None) -> RssNewsSource | None:
    config_path = _news_source_config_path(path)
    if not config_path.exists():
        return None
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise DataError(f"新闻源配置读取失败: {config_path}; {exc}") from exc
    rss_cfg = payload.get("rss", {}) if isinstance(payload, dict) else {}
    if not isinstance(rss_cfg, dict) or not bool(rss_cfg.get("enabled", False)):
        return None
    feeds = _rss_feeds_from_payload(rss_cfg)
    if not feeds:
        return None
    timeout_seconds = float(rss_cfg.get("timeout_seconds", 6.0) or 6.0)
    max_concurrency = int(rss_cfg.get("max_concurrency", 4) or 4)
    return RssNewsSource(
        feeds,
        timeout_seconds=timeout_seconds,
        max_concurrency=max_concurrency,
    )


def rss_news_runtime_summary(path: str | None = None) -> RssNewsRuntimeSummary:
    config_path = _news_source_config_path(path)
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return RssNewsRuntimeSummary(
            enabled=False,
            feed_count=0,
            feed_names=(),
            covered_triggers=(),
            missing_triggers=tuple(_RSS_CORE_TRIGGER_KEYWORDS),
            keyword_gated_feeds=0,
        )
    rss_cfg = payload.get("rss", {}) if isinstance(payload, dict) else {}
    if not isinstance(rss_cfg, dict) or not bool(rss_cfg.get("enabled", False)):
        return RssNewsRuntimeSummary(
            enabled=False,
            feed_count=0,
            feed_names=(),
            covered_triggers=(),
            missing_triggers=tuple(_RSS_CORE_TRIGGER_KEYWORDS),
            keyword_gated_feeds=0,
        )
    feeds = _rss_feeds_from_payload(rss_cfg)
    keyword_blob = " ".join(
        " ".join(feed.keywords).casefold() for feed in feeds if feed.enabled
    )
    covered = tuple(
        trigger
        for trigger, keywords in _RSS_CORE_TRIGGER_KEYWORDS.items()
        if any(keyword.casefold() in keyword_blob for keyword in keywords)
    )
    missing = tuple(
        trigger for trigger in _RSS_CORE_TRIGGER_KEYWORDS if trigger not in set(covered)
    )
    return RssNewsRuntimeSummary(
        enabled=True,
        feed_count=len(feeds),
        feed_names=tuple(feed.name for feed in feeds),
        covered_triggers=covered,
        missing_triggers=missing,
        keyword_gated_feeds=sum(1 for feed in feeds if feed.keywords),
    )


def _news_source_config_path(path: str | None) -> Path:
    configured = str(path or os.getenv("AQSP_NEWS_SOURCE_CONFIG", "")).strip()
    if configured:
        raw = Path(configured).expanduser()
    else:
        project_root = Path(
            os.getenv("AQSP_PROJECT_ROOT", Path(__file__).resolve().parents[3])
        )
        raw = project_root / "config" / "news_sources.yaml"
    if raw.is_absolute():
        return raw.resolve(strict=False)
    project_root = Path(
        os.getenv("AQSP_PROJECT_ROOT", Path(__file__).resolve().parents[3])
    )
    return (project_root / raw).resolve(strict=False)


def _rss_feeds_from_payload(payload: dict[object, object]) -> tuple[RssFeedConfig, ...]:
    feeds_raw = payload.get("feeds", ())
    if not isinstance(feeds_raw, list):
        return ()
    feeds: list[RssFeedConfig] = []
    for item in feeds_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        url = str(item.get("url", "") or "").strip()
        if not name or not url:
            continue
        feeds.append(
            RssFeedConfig(
                name=name,
                url=url,
                category=str(item.get("category", "") or "").strip(),
                enabled=bool(item.get("enabled", True)),
                symbols=_as_text_tuple(item.get("symbols", ())),
                keywords=_as_text_tuple(item.get("keywords", ())),
                max_items=max(
                    1, int(item.get("max_items", payload.get("max_items", 20)) or 20)
                ),
                region=_news_region(
                    str(item.get("region", "") or "").strip(),
                    category=str(item.get("category", "") or "").strip(),
                    name=name,
                ),
            )
        )
    return tuple(feeds)


def _parse_rss_xml(
    content: bytes,
    *,
    source_name: str,
    category: str,
    region: NewsSourceRegion,
    symbols: tuple[str, ...],
    keywords: tuple[str, ...],
    max_items: int,
) -> pd.DataFrame:
    root = ET.fromstring(content)
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    rows: list[dict[str, object]] = []
    max_matched_items = max(1, max_items)
    for item in items:
        title = _xml_text(item, ("title", "{http://www.w3.org/2005/Atom}title"))
        link = _xml_link(item)
        if not title:
            continue
        summary = _xml_text(
            item,
            (
                "description",
                "summary",
                "content",
                "{http://www.w3.org/2005/Atom}summary",
                "{http://www.w3.org/2005/Atom}content",
            ),
        )
        if not _rss_item_matches_keywords(
            title=title, summary=summary, keywords=keywords
        ):
            continue
        published_at = _normalize_rss_time(
            _xml_text(
                item,
                (
                    "pubDate",
                    "published",
                    "updated",
                    "{http://www.w3.org/2005/Atom}published",
                    "{http://www.w3.org/2005/Atom}updated",
                ),
            )
        )
        rows.append(
            {
                # Keep the canonical keys alongside legacy Chinese columns so
                # downstream adapters can trace title/source/time/link without
                # guessing which feed schema was returned.
                "title": title,
                "source": source_name,
                "published_at": published_at,
                "url": link,
                "summary": summary,
                "标题": title,
                "来源": source_name,
                "时间": published_at,
                "链接": link,
                "摘要": summary,
                "category": category,
                "source_group": "rss",
                "source_region": region,
                "symbols": ",".join(symbols),
                "keywords": ",".join(keywords),
                "keyword_matched": _rss_keyword_match_text(
                    title=title,
                    summary=summary,
                    keywords=keywords,
                ),
            }
        )
        if len(rows) >= max_matched_items:
            break
    return pd.DataFrame(rows)


def _xml_text(item: ET.Element, tags: tuple[str, ...]) -> str:
    for tag in tags:
        child = item.find(tag)
        if child is not None and child.text:
            return str(child.text).strip()
    return ""


def _xml_link(item: ET.Element) -> str:
    link = _xml_text(item, ("link", "{http://www.w3.org/2005/Atom}link"))
    if link:
        return link
    for child in item.findall("{http://www.w3.org/2005/Atom}link"):
        href = str(child.attrib.get("href", "") or "").strip()
        if href:
            return href
    return ""


def _normalize_rss_time(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_SHANGHAI_TZ)
        return parsed.isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_SHANGHAI_TZ)
        return parsed.isoformat(timespec="seconds")
    except ValueError:
        return text


def _rss_item_matches_keywords(
    *,
    title: str,
    summary: str,
    keywords: tuple[str, ...],
) -> bool:
    if not keywords:
        return True
    return bool(
        _rss_keyword_match_text(title=title, summary=summary, keywords=keywords)
    )


def _rss_keyword_match_text(
    *,
    title: str,
    summary: str,
    keywords: tuple[str, ...],
) -> str:
    # URLs identify the source only; host/path terms are not news content.
    haystack = " ".join((title, summary)).casefold()
    matches = [
        keyword
        for keyword in keywords
        if str(keyword or "").strip().casefold() in haystack
    ]
    return ",".join(matches[:5])


def _row_matches_symbol(row: pd.Series, symbol: str) -> bool:
    searchable_keys = (
        "symbol",
        "代码",
        "股票代码",
        "新闻标题",
        "公告标题",
        "标题",
        "title",
        "内容",
        "摘要",
        "链接",
        "url",
    )
    blob = " ".join(str(row.get(key, "") or "") for key in searchable_keys)
    return symbol in blob


def _attach_source_warnings(frames: list[pd.DataFrame], errors: list[str]) -> None:
    warnings = tuple(str(item).strip() for item in errors if str(item).strip())[:5]
    if not warnings:
        return
    for frame in frames:
        existing = tuple(frame.attrs.get("aqsp_warnings", ()) or ())
        frame.attrs["aqsp_warnings"] = (*existing, *warnings)


def _attach_source_health(
    frames: list[pd.DataFrame], health: Sequence[NewsSourceHealth]
) -> None:
    if not health:
        return
    payload = tuple(
        {
            "name": item.name,
            "region": item.region,
            "status": item.status,
            "attempted": item.attempted,
            "successful": item.successful,
            "row_count": item.row_count,
            "fetched_at": item.fetched_at,
            "warnings": item.warnings,
        }
        for item in health
    )
    for frame in frames:
        frame.attrs["aqsp_source_health"] = payload
        frame.attrs["aqsp_source_status"] = _aggregate_source_status(health)


def _aggregate_source_status(
    health: Sequence[NewsSourceHealth],
) -> NewsSourceStatus:
    statuses = tuple(item.status for item in health)
    if not statuses:
        return "failed"
    if all(status == "ok" for status in statuses):
        return "ok"
    if any(status == "ok" for status in statuses):
        return "partial"
    if any(status == "partial" for status in statuses):
        return "partial"
    if all(status == "empty" for status in statuses):
        return "empty"
    if all(status == "timeout" for status in statuses):
        return "timeout"
    return "failed"


def _status_from_exception(exc: BaseException) -> NewsSourceStatus:
    text = str(exc).casefold()
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout"
    return "failed"


def _news_region(value: str, *, category: str, name: str) -> NewsSourceRegion:
    normalized = str(value or "").strip().casefold()
    if normalized in {"domestic", "cn", "china", "国内"}:
        return "domestic"
    if normalized in {"international", "global", "overseas", "海外"}:
        return "international"
    if normalized in {"mixed", "both", "混合"}:
        return "mixed"
    text = f"{category} {name}".casefold()
    if any(
        token in text
        for token in (
            "global",
            "海外",
            "美联储",
            "sec",
            "欧洲央行",
            "nasa",
            "marketwatch",
            "nvidia",
        )
    ):
        return "international"
    return "domestic"


def _normalize_region(value: str) -> NewsSourceRegion:
    return _news_region(value, category="", name="")


def _region_for_source_name(name: str) -> NewsSourceRegion:
    return _news_region("", category="", name=name)


def _as_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()
