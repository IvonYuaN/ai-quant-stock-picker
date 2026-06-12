from __future__ import annotations

import signal
import threading
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.presentation import normalize_research_tone

Impact = Literal["positive", "negative", "neutral"]


@dataclass(frozen=True)
class NewsCatalystConfig:
    symbols: tuple[str, ...] = ()
    max_symbol_news: int = 5
    max_global_news: int = 20
    max_events: int = 8
    min_confidence: float = 0.35
    enable_llm_review: bool = False
    source_timeout_seconds: float = 8.0
    llm_timeout_seconds: float = 8.0
    max_llm_review_events: int = 3


@dataclass(frozen=True)
class CatalystEvent:
    title: str
    source: str
    published_at: str
    symbol: str = ""
    name: str = ""
    impact: Impact = "neutral"
    category: str = "消息"
    weight: int = 1
    confidence: float = 0.0
    source_count: int = 1
    verification: str = "待证实"
    llm_review: str = ""
    reason: str = ""
    url: str = ""


@dataclass(frozen=True)
class CatalystReport:
    date: str
    generated_at: str
    events: tuple[CatalystEvent, ...]
    source_status: str
    warnings: tuple[str, ...] = ()


Fetcher = Callable[[str, int], pd.DataFrame]


POSITIVE_PATTERNS: tuple[tuple[str, str, int], ...] = (
    ("涨价|提价|价格上调|报价上调|涨幅|涨超|缺货|供不应求", "涨价/供需催化", 5),
    ("扩产受限|停产|限产|供给收缩|库存低位|排产紧张", "供给收缩", 4),
    ("政策支持|刺激|补贴|利好|国常会|发改委|工信部", "政策催化", 4),
    ("中标|大单|订单|签订合同|采购|定点|放量", "订单/需求验证", 4),
    ("业绩预增|扭亏|超预期|利润增长|收入增长", "业绩催化", 4),
    ("回购|增持|并购|重组|注入|战略合作", "资本运作", 3),
)

NEGATIVE_PATTERNS: tuple[tuple[str, str, int], ...] = (
    ("减持|清仓|套现|解禁|质押|爆仓", "股东/筹码风险", 5),
    ("立案|调查|处罚|问询函|监管|诉讼|仲裁", "监管/合规风险", 5),
    ("事故|停工|停产|召回|安全隐患|污染", "经营事故", 5),
    ("制裁|限制|禁令|断供|关税|出口管制", "外部冲击", 4),
    ("业绩下滑|亏损|不及预期|预亏|暴雷", "业绩风险", 4),
    ("价格下跌|降价|需求疲弱|库存高企|产能过剩", "供需转弱", 4),
)


def build_catalyst_report(
    *,
    symbols: Sequence[str] = (),
    symbol_names: dict[str, str] | None = None,
    fetch_symbol_news: Fetcher | None = None,
    fetch_global_news: Callable[[int], pd.DataFrame] | None = None,
    config: NewsCatalystConfig | None = None,
) -> CatalystReport:
    cfg = config or NewsCatalystConfig(symbols=tuple(symbols))
    names = symbol_names or {}
    warnings: list[str] = []
    rows: list[CatalystEvent] = []

    symbol_fetcher = fetch_symbol_news or _akshare_symbol_news
    global_fetcher = fetch_global_news or _akshare_global_news

    for symbol in tuple(symbols or cfg.symbols):
        try:
            df = _call_fetcher_with_timeout(
                lambda: symbol_fetcher(symbol, cfg.max_symbol_news),
                timeout_seconds=cfg.source_timeout_seconds,
            )
        except Exception as exc:
            warnings.append(f"{symbol} 个股新闻获取失败: {exc}")
            continue
        rows.extend(
            _events_from_rows(
                _iter_news_rows(df.head(cfg.max_symbol_news)),
                symbol=symbol,
                name=names.get(symbol, ""),
            )
        )

    try:
        global_df = _call_fetcher_with_timeout(
            lambda: global_fetcher(cfg.max_global_news),
            timeout_seconds=cfg.source_timeout_seconds,
        )
    except Exception as exc:
        warnings.append(f"全市场快讯获取失败: {exc}")
        global_df = pd.DataFrame()
    rows.extend(_events_from_rows(_iter_news_rows(global_df.head(cfg.max_global_news))))

    deduped = _merge_events(rows)
    reviewed = _review_events(
        deduped,
        enable_llm=cfg.enable_llm_review,
        timeout_seconds=cfg.llm_timeout_seconds,
        max_events=cfg.max_llm_review_events,
    )
    filtered = [item for item in reviewed if item.confidence >= cfg.min_confidence]
    ranked = sorted(
        filtered,
        key=lambda item: (item.weight, item.confidence, item.source_count),
        reverse=True,
    )
    status = "ok" if ranked else "empty"
    if warnings and ranked:
        status = "partial"
    elif warnings:
        status = "failed"
    return CatalystReport(
        date=today_shanghai().isoformat(),
        generated_at=now_shanghai().isoformat(timespec="seconds"),
        events=tuple(ranked[: cfg.max_events]),
        source_status=status,
        warnings=tuple(warnings[:5]),
    )


def format_catalyst_notification(report: CatalystReport) -> str:
    has_events = bool(report.events)
    has_warnings = bool(report.warnings)
    if has_events:
        lead = report.events[0]
        lead_target = (
            f"{lead.symbol} {lead.name}".strip() if lead.symbol else "市场/行业"
        )
        lead_line = f"今天先看 {lead_target} 的 {lead.category}：{lead.title[:36]}"
    elif report.source_status == "failed":
        lead_line = "本次消息源没有按时返回，今天不要用这条通知下结论。"
    elif report.source_status == "partial":
        lead_line = "消息只抓到一部分，先把它当提示，不当结论。"
    else:
        lead_line = "今天没有筛出足够强的消息催化，先按主链和量价节奏看盘。"

    lines = [
        f"# 消息面雷达-{report.date}",
        "",
        "> 🧭 这条通知只帮你回答两件事：今天有没有值得先看的消息，以及开盘后怎么验证。它不替代主报告结论，也不是交易指令；多源交叉或公告来源优先。",
        "",
        "## 👀 一眼先看",
        "",
        f"- {lead_line}",
        f"- 当前状态: {_source_status_label(report.source_status)}",
        f"- 生成时间: {report.generated_at}",
        "",
        "## 🧨 高影响事件",
        "",
    ]
    if not report.events:
        if report.source_status == "failed":
            lines.extend(
                [
                    "- 本次没拿到可靠消息，不代表市场一定没事，只代表这条消息通知今天不能用。",
                    "- 今天盘前先回到主链报告，优先看今日重点名单、继续观察名单和现在卡在哪。",
                    "- 如果你担心错过突发，开盘后再人工补看公告、财联社、交易所公告。 ",
                ]
            )
        else:
            lines.extend(
                [
                    "- 今天没筛出足够强的高影响消息，先以主链量价和风险约束为准。",
                    "- 这通常意味着没有明确的消息催化主线，不要为了“有消息”硬找方向。",
                ]
            )
    else:
        for index, event in enumerate(report.events, start=1):
            lines.extend(_event_card_lines(index, event))
            lines.append("")

    lines.extend(
        [
            "",
            "## ✅ 开盘怎么用",
            "",
            "1. 先只看前两条高影响事件，不要一早被一堆标题带偏。",
            "2. 如果是涨价、政策、订单这类利好，开盘后先看相关股票和板块有没有一起放量走强。",
            "3. 如果是减持、监管、事故这类利空，先把它当风险，优先回避，不要和热度对赌。",
            "4. 单一媒体标题只能算线索；看到公告、交易所、公司原文，可信度才算明显提升。",
            "5. 如果这条通知失败或为空，今天就回到主链报告，不要因为“没有消息”乱改计划。",
            "",
            "## 🧾 这条通知靠不靠谱",
            "",
            f"- 状态: {report.source_status}",
            f"- 是否抓到高影响事件: {'是' if has_events else '否'}",
            f"- 是否有抓取告警: {'是' if has_warnings else '否'}",
        ]
    )
    if report.warnings:
        lines.append("- 告警: " + "；".join(report.warnings[:3]))
    return normalize_research_tone("\n".join(lines))


def _source_status_label(status: str) -> str:
    return {
        "ok": "已拿到可用消息",
        "partial": "只拿到部分消息",
        "empty": "没筛出足够强的消息",
        "failed": "抓取失败，本次通知不可直接使用",
    }.get(status, status or "未知")


def _event_card_lines(index: int, event: CatalystEvent) -> list[str]:
    """把单个事件渲染成窄屏友好的卡片，不用表格，避免列被压成竖排单字。"""
    impact = {"positive": "🟢 利好", "negative": "🔴 利空", "neutral": "⚪ 中性"}[
        event.impact
    ]
    target = f"{event.symbol} {event.name}".strip() if event.symbol else "市场/行业"
    title = _inline(event.title)
    lines = [
        f"**{index}. {impact} ｜ {_inline(target)}**",
        f"- 事件: {title}",
        f"- 类型: {_inline(event.category)} ｜ 可信度: {event.confidence:.0%}（{_inline(event.verification)}）",
        f"- 来源: {_inline(event.source)} ｜ 时间: {_inline(event.published_at)}",
    ]
    if event.url:
        lines.append(f"- 原文: {_inline(event.url)}")
    reason = _inline(event.reason)
    if reason and reason != "-":
        lines.append(f"- 复核重点: {reason}")
    return lines


def _events_from_rows(
    rows: Iterable[dict[str, str]],
    *,
    symbol: str = "",
    name: str = "",
) -> list[CatalystEvent]:
    events: list[CatalystEvent] = []
    for row in rows:
        title = row.get("title", "")
        event = _classify_title(title)
        if event is None:
            continue
        category, impact, weight, reason = event
        events.append(
            CatalystEvent(
                title=title,
                source=row.get("source", ""),
                published_at=row.get("published_at", ""),
                symbol=symbol,
                name=name,
                impact=impact,
                category=category,
                weight=weight,
                confidence=_base_confidence(row),
                verification=_verification_label(row),
                reason=reason,
                url=row.get("url", ""),
            )
        )
    return events


def _classify_title(title: str) -> tuple[str, Impact, int, str] | None:
    clean = str(title or "").strip()
    if not clean:
        return None
    import re

    for pattern, category, weight in NEGATIVE_PATTERNS:
        if re.search(pattern, clean):
            return (
                category,
                "negative",
                weight,
                "先按风险事件复核，确认是否影响短线承接",
            )
    for pattern, category, weight in POSITIVE_PATTERNS:
        if re.search(pattern, clean):
            return (
                category,
                "positive",
                weight,
                "关注是否出现板块扩散、量价确认和连续发酵",
            )
    return None


def _iter_news_rows(df: pd.DataFrame) -> Iterable[dict[str, str]]:
    if df is None or df.empty:
        return ()
    rows: list[dict[str, str]] = []
    for row in df.to_dict(orient="records"):
        title = _first_text(row, ("新闻标题", "标题", "title", "内容", "摘要"))
        if not title:
            continue
        rows.append(
            {
                "title": title,
                "source": _first_text(row, ("文章来源", "媒体", "source", "来源")),
                "published_at": _first_text(row, ("发布时间", "时间", "date", "日期")),
                "url": _first_text(row, ("新闻链接", "链接", "url")),
            }
        )
    return tuple(rows)


def _first_text(row: dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = row.get(key)
        text = "" if value is None else str(value).strip()
        if text and text.lower() != "nan":
            return text
    return ""


def _merge_events(events: Sequence[CatalystEvent]) -> tuple[CatalystEvent, ...]:
    merged: dict[str, CatalystEvent] = {}
    for event in events:
        key = _event_merge_key(event)
        existing = merged.get(key)
        if existing is None:
            merged[key] = event
            continue
        merged[key] = CatalystEvent(
            title=existing.title
            if len(existing.title) <= len(event.title)
            else event.title,
            source="、".join(_dedupe_texts((existing.source, event.source))),
            published_at=existing.published_at or event.published_at,
            symbol=existing.symbol or event.symbol,
            name=existing.name or event.name,
            impact=existing.impact,
            category=existing.category,
            weight=max(existing.weight, event.weight) + 1,
            confidence=min(1.0, max(existing.confidence, event.confidence) + 0.18),
            source_count=existing.source_count + event.source_count,
            verification="多源交叉"
            if existing.source != event.source
            else existing.verification,
            reason=existing.reason,
            url=existing.url or event.url,
        )
    return tuple(merged.values())


def _review_events(
    events: Sequence[CatalystEvent],
    *,
    enable_llm: bool,
    timeout_seconds: float,
    max_events: int,
) -> tuple[CatalystEvent, ...]:
    if not enable_llm or not events:
        return tuple(events)
    from aqsp.utils.llm_safe import llm_call_or_fallback

    reviewed: list[CatalystEvent] = []
    review_limit = max(0, max_events)
    for event in events[:review_limit]:
        prompt = (
            "你是A股消息面复核助手。请判断下面新闻标题是否可能是短线高影响事件。"
            "只输出一行：可信度=0-100; 影响=利好/利空/中性; 理由=不超过30字。"
            "如果只是标题党、传闻或缺少原始来源，请降低可信度。\n"
            f"标题: {event.title}\n来源: {event.source}\n类型: {event.category}\n"
        )
        fallback = (
            f"可信度={event.confidence:.0%}; 影响={event.impact}; "
            "理由=未启用模型复核，按多源和关键词降级判断"
        )
        result = llm_call_or_fallback(
            prompt=prompt,
            fallback=fallback,
            enable_llm=True,
            caller="news_catalyst_review",
            timeout_s=max(1.0, timeout_seconds),
        )
        llm_conf = _parse_llm_confidence(result.text)
        confidence = event.confidence
        if llm_conf is not None:
            confidence = round((event.confidence + llm_conf) / 2, 3)
        reviewed.append(
            CatalystEvent(
                **{
                    **event.__dict__,
                    "confidence": confidence,
                    "llm_review": result.text[:160],
                    "verification": (
                        "模型复核/降级"
                        if result.degraded
                        else f"模型复核/{event.verification}"
                    ),
                }
            )
        )
    reviewed.extend(events[review_limit:])
    return tuple(reviewed)


def _base_confidence(row: dict[str, str]) -> float:
    title = row.get("title", "")
    source = row.get("source", "")
    confidence = 0.38
    if any(token in source for token in ("公告", "交易所", "巨潮", "公司", "证券报")):
        confidence += 0.28
    if any(token in title for token in ("据悉", "传", "网传", "市场消息")):
        confidence -= 0.18
    if row.get("url"):
        confidence += 0.08
    return max(0.05, min(0.95, confidence))


def _verification_label(row: dict[str, str]) -> str:
    source = row.get("source", "")
    if any(token in source for token in ("公告", "交易所", "巨潮", "公司")):
        return "接近原始来源"
    if any(token in source for token in ("证券报", "财联社", "东财", "同花顺", "新浪")):
        return "媒体来源"
    return "待证实"


def _event_merge_key(event: CatalystEvent) -> str:
    return "".join(ch for ch in event.title if "\u4e00" <= ch <= "\u9fff")[:24]


def _dedupe_texts(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return tuple(out)


def _parse_llm_confidence(text: str) -> float | None:
    import re

    match = re.search(r"可信度\s*[=:：]\s*(\d{1,3})", text)
    if not match:
        return None
    value = max(0, min(100, int(match.group(1))))
    return value / 100


def _inline(value: object) -> str:
    """把字段压成单行文本，去掉换行；保留竖线无害（不再走表格）。"""
    return str(value or "").replace("\n", " ").strip() or "-"


def _call_fetcher_with_timeout(
    fetch: Callable[[], pd.DataFrame],
    *,
    timeout_seconds: float,
) -> pd.DataFrame:
    if threading.current_thread() is threading.main_thread() and hasattr(
        signal,
        "SIGALRM",
    ):
        return _call_fetcher_with_signal_timeout(
            fetch,
            timeout_seconds=timeout_seconds,
        )
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fetch)
    try:
        result = future.result(timeout=max(0.1, timeout_seconds))
    except FutureTimeoutError as exc:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise TimeoutError(f"消息源超过 {timeout_seconds:.1f}s 未返回") from exc
    finally:
        if future.done():
            executor.shutdown(wait=False, cancel_futures=True)
    if result is None:
        return pd.DataFrame()
    return result


def _call_fetcher_with_signal_timeout(
    fetch: Callable[[], pd.DataFrame],
    *,
    timeout_seconds: float,
) -> pd.DataFrame:
    timeout = max(0.1, float(timeout_seconds))

    def _raise_timeout(_signum, _frame) -> None:
        raise TimeoutError(f"消息源超过 {timeout_seconds:.1f}s 未返回")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        result = fetch()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    if result is None:
        return pd.DataFrame()
    return result


def _fetch_optional_frame(
    fetch: Callable[[], pd.DataFrame], timeout_seconds: float
) -> pd.DataFrame:
    try:
        return _call_fetcher_with_timeout(fetch, timeout_seconds=timeout_seconds)
    except Exception:
        return pd.DataFrame()


def _akshare_symbol_news(symbol: str, limit: int) -> pd.DataFrame:
    import akshare as ak

    frames: list[pd.DataFrame] = []
    fetchers = (
        lambda: ak.stock_news_em(symbol=symbol),
        lambda: ak.stock_individual_notice_report(symbol=symbol),
        lambda: ak.stock_research_report_em(symbol=symbol),
    )
    for fetch in fetchers:
        df = _fetch_optional_frame(fetch, timeout_seconds=6.0)
        if df is not None and not df.empty:
            frames.append(df.head(limit))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).head(limit * 3)


def _akshare_global_news(limit: int) -> pd.DataFrame:
    import akshare as ak

    fetchers = (
        lambda: ak.stock_info_global_cls(),
        lambda: ak.stock_info_global_em(),
        lambda: ak.stock_info_global_ths(),
        lambda: ak.stock_info_global_futu(),
        lambda: ak.stock_info_global_sina(),
        lambda: ak.news_cctv(),
        lambda: ak.news_economic_baidu(),
        lambda: ak.stock_notice_report(),
    )
    frames: list[pd.DataFrame] = []
    for fetch in fetchers:
        df = _fetch_optional_frame(fetch, timeout_seconds=6.0)
        if df is not None and not df.empty:
            frames.append(df.head(limit))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).head(limit)
