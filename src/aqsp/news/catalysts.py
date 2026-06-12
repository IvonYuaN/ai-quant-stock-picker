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
    min_confidence: float = 0.45
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
    ("涨价|提价|价格上调|报价上调|缺货|供不应求", "涨价/供需催化", 5),
    ("扩产受限|停产|限产|供给收缩|库存低位|排产紧张", "供给收缩", 4),
    (
        "政策支持|补贴|国常会|发改委|工信部|行动方案|指导意见|以旧换新|设备更新",
        "政策催化",
        4,
    ),
    (
        "中标|大单|订单|签订合同|采购|定点|销量放量|出货放量|需求放量",
        "订单/需求验证",
        4,
    ),
    ("业绩预增|扭亏|超预期|利润增长|收入增长", "业绩催化", 4),
    ("回购|增持|并购|重组|注入|战略合作", "资本运作", 3),
)

NEGATIVE_PATTERNS: tuple[tuple[str, str, int], ...] = (
    ("减持|清仓|套现|解禁|质押|爆仓", "股东/筹码风险", 5),
    ("立案|调查|处罚|问询函|监管处罚|监管问询|诉讼|仲裁", "监管/合规风险", 5),
    ("事故|停工|停产|召回|安全隐患|污染", "经营事故", 5),
    ("制裁|限制|禁令|断供|关税|出口管制", "外部冲击", 4),
    ("业绩下滑|亏损|不及预期|预亏|暴雷", "业绩风险", 4),
    ("价格下跌|降价|需求疲弱|库存高企|产能过剩", "供需转弱", 4),
)

_AUTHORITATIVE_SOURCE_TOKENS: tuple[str, ...] = (
    "公告",
    "交易所",
    "巨潮",
    "公司",
    "证监会",
)

_MEDIA_SOURCE_TOKENS: tuple[str, ...] = (
    "新华社",
    "央视",
    "证券报",
    "财联社",
    "东财",
    "同花顺",
    "新浪",
)

_SOURCE_BY_URL_TOKEN: tuple[tuple[str, str], ...] = (
    ("10jqka.com.cn", "同花顺"),
    ("eastmoney.com", "东财"),
    ("futunn.com", "富途"),
    ("cls.cn", "财联社"),
    ("cnstock.com", "证券报"),
    ("xinhua", "新华社"),
    ("cctv", "央视"),
    ("cninfo.com.cn", "巨潮公告"),
    ("sse.com.cn", "上交所公告"),
    ("szse.cn", "深交所公告"),
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
        warnings.extend(_frame_warnings(df, prefix=f"{symbol} 个股新闻"))
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
    warnings.extend(_frame_warnings(global_df, prefix="全市场快讯"))
    warnings = list(_dedupe_texts(warnings))
    raw_news_count = len(_iter_news_rows(global_df))
    rows.extend(_events_from_rows(_iter_news_rows(global_df.head(cfg.max_global_news))))

    deduped = _merge_events(rows)
    pre_ranked = tuple(
        sorted(
            deduped,
            key=lambda item: (item.weight, item.confidence, item.source_count),
            reverse=True,
        )
    )
    reviewed = _review_events(
        pre_ranked,
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
    status = _report_source_status(
        has_ranked=bool(ranked),
        has_raw_news=raw_news_count > 0,
        has_warnings=bool(warnings),
    )
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
        lead_target = _event_target(lead)
        lead_line = (
            f"今天先看 {lead_target} 的 {lead.category}：{_short_text(lead.title, 36)}"
        )
    elif report.source_status == "failed":
        lead_line = "本次消息源没有按时返回，今天不要用这条通知下结论。"
    elif report.source_status == "partial":
        lead_line = "消息只抓到一部分，先把它当提示，不当结论。"
    else:
        lead_line = "今天没有筛出足够强的消息催化，先按主链和量价节奏看盘。"

    lines = [
        f"# 消息面雷达-{report.date}｜{_report_title_status(report)}",
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
        lines.append(
            "- 告警: "
            + "；".join(
                _safe_warning(item) for item in _display_warnings(report.warnings)
            )
        )
    return normalize_research_tone("\n".join(lines))


def _source_status_label(status: str) -> str:
    return {
        "ok": "已拿到可用消息",
        "partial": "只拿到部分消息",
        "empty": "没筛出足够强的消息",
        "failed": "抓取失败，本次通知不可直接使用",
    }.get(status, status or "未知")


def _report_source_status(
    *,
    has_ranked: bool,
    has_raw_news: bool,
    has_warnings: bool,
) -> str:
    if has_ranked:
        return "partial" if has_warnings else "ok"
    if has_raw_news:
        return "partial" if has_warnings else "empty"
    return "failed" if has_warnings else "empty"


def _report_title_status(report: CatalystReport) -> str:
    if report.events:
        lead = report.events[0]
        return f"{lead.category}{'/利空' if lead.impact == 'negative' else ''}"
    if report.source_status == "failed":
        return "抓取失败"
    if report.source_status == "partial":
        return "部分消息"
    return "无强催化"


def _event_card_lines(index: int, event: CatalystEvent) -> list[str]:
    """把单个事件渲染成窄屏友好的卡片，不用表格，避免列被压成竖排单字。"""
    impact = {"positive": "🟢 利好", "negative": "🔴 利空", "neutral": "⚪ 中性"}[
        event.impact
    ]
    target = _event_target(event)
    title = _short_text(event.title, 42)
    lines = [
        f"**{index}. {impact} ｜ {_inline(target)}**",
        f"- 事件: {title}",
        f"- 类型: {_inline(event.category)} ｜ 可信度: {event.confidence:.0%}（{_inline(event.verification)}）",
        f"- 来源: {_inline(event.source)} ｜ 时间: {_inline(event.published_at)}",
        f"- 怎么验证: {_verification_hint(event)}",
        "- 不要做: 只凭标题追高；至少等公告/多源交叉/板块和量价一起确认。",
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
                name=name or _name_from_title(title),
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


def _event_target(event: CatalystEvent) -> str:
    target = f"{event.symbol} {event.name}".strip()
    return target or _name_from_title(event.title) or "市场/行业"


def _name_from_title(title: str) -> str:
    import re

    clean = str(title or "").strip()
    match = re.match(r"^([\u4e00-\u9fffA-Za-z0-9]{2,12})[:：]", clean)
    if not match:
        return ""
    name = match.group(1)
    if name in {"消息人士", "市场消息", "快讯"} or name.startswith("据"):
        return ""
    return name


def _classify_title(title: str) -> tuple[str, Impact, int, str] | None:
    clean = str(title or "").strip()
    if not clean:
        return None
    import re

    if _is_market_price_action_noise(clean):
        return None
    if _is_non_actionable_discipline_news(clean):
        return None
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


def _is_market_price_action_noise(title: str) -> bool:
    import re

    price_action = re.search(
        r"ETF|指数|盘中|涨超|涨逾|涨幅|大涨|领涨|转跌|收涨|成交|放量冲击|"
        r"涨停|封板|冲板|拉升|走强|异动",
        title,
    )
    if not price_action:
        return False
    if re.search(r"传闻|网传|市场消息|受.*刺激|受.*利好", title):
        return True
    fundamental = re.search(
        r"公告|交易所|涨价|提价|报价上调|价格上调|缺货|供不应求|政策支持|补贴|"
        r"中标|订单|签订合同|业绩预增|回购|增持|并购|重组|减持|立案|调查|"
        r"处罚|事故|停产|制裁|出口管制|预亏|亏损",
        title,
    )
    return fundamental is None


def _is_non_actionable_discipline_news(title: str) -> bool:
    import re

    if not re.search(r"纪律审查|监察调查|严重违纪违法", title):
        return False
    listed_context = re.search(
        r"上市公司|股份有限公司|证券|股票|公告|交易所|证监|董监高|实控人|控股股东",
        title,
    )
    return listed_context is None


def _iter_news_rows(df: pd.DataFrame) -> Iterable[dict[str, str]]:
    if df is None or df.empty:
        return ()
    rows: list[dict[str, str]] = []
    for row in df.to_dict(orient="records"):
        title = _first_text(
            row, ("新闻标题", "公告标题", "标题", "title", "内容", "摘要")
        )
        if not title:
            continue
        url = _first_text(row, ("新闻链接", "链接", "url", "公告链接"))
        source = _first_text(
            row, ("文章来源", "媒体", "source", "来源", "公告类型")
        ) or _source_from_url(url)
        rows.append(
            {
                "title": title,
                "source": source,
                "published_at": _first_text(
                    row, ("发布时间", "时间", "date", "日期", "公告日期")
                ),
                "url": url,
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


def _source_from_url(url: str) -> str:
    clean = str(url or "").lower()
    if not clean:
        return ""
    for token, source in _SOURCE_BY_URL_TOKEN:
        if token in clean:
            return source
    return ""


def _merge_events(events: Sequence[CatalystEvent]) -> tuple[CatalystEvent, ...]:
    merged: list[CatalystEvent] = []
    for event in events:
        existing_index = next(
            (
                index
                for index, existing in enumerate(merged)
                if _events_can_merge(existing, event)
            ),
            None,
        )
        if existing_index is None:
            merged.append(event)
            continue
        existing = merged[existing_index]
        merged[existing_index] = CatalystEvent(
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
    return tuple(merged)


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
    if any(token in source for token in _AUTHORITATIVE_SOURCE_TOKENS):
        confidence += 0.28
    elif any(token in source for token in _MEDIA_SOURCE_TOKENS):
        confidence += 0.12
    if any(token in title for token in ("据悉", "传", "网传", "市场消息", "消息人士")):
        confidence -= 0.18
    if row.get("url"):
        confidence += 0.08
    return max(0.05, min(0.95, confidence))


def _verification_label(row: dict[str, str]) -> str:
    source = row.get("source", "")
    if any(token in source for token in _AUTHORITATIVE_SOURCE_TOKENS):
        return "接近原始来源"
    if any(token in source for token in _MEDIA_SOURCE_TOKENS):
        return "媒体来源"
    return "待证实"


def _events_can_merge(left: CatalystEvent, right: CatalystEvent) -> bool:
    left_title = _normalized_title_key(left.title)
    right_title = _normalized_title_key(right.title)
    if left.symbol and right.symbol and left.symbol != right.symbol:
        return False
    if left_title and left_title == right_title:
        return True
    if left.impact != right.impact or left.category != right.category:
        return False
    left_target = _event_target(left)
    right_target = _event_target(right)
    if left_target == "市场/行业" or right_target == "市场/行业":
        return False
    if left_target != right_target:
        return False
    return _title_overlap_ratio(left_title, right_title) >= 0.62


def _normalized_title_key(title: str) -> str:
    import re

    text = "".join(ch for ch in str(title or "") if "\u4e00" <= ch <= "\u9fff")
    text = re.sub(r"\d+年\d+月\d+日|\d+月\d+日|\d+年第\d+次", "", text)
    text = re.sub(r"召开|定于|公司|股份|购买资产|募集配套资金", "", text)
    return text[:36]


def _title_overlap_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_tokens = set(left)
    right_tokens = set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(
        1, min(len(left_tokens), len(right_tokens))
    )


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


def _safe_warning(value: object) -> str:
    text = _inline(value).replace("<", "＜").replace(">", "＞")
    lower = text.lower()
    if (
        "httpsconnectionpool" in lower
        or "remote end closed connection" in lower
        or "connection aborted" in lower
        or "max retries exceeded" in lower
        or "read timed out" in lower
        or "timed out" in lower
    ):
        return "部分消息源超时或连接中断，已降级使用其它来源"
    return text[:120] + ("..." if len(text) > 120 else "")


def _display_warnings(warnings: Sequence[str], limit: int = 3) -> tuple[str, ...]:
    displayed: list[str] = []
    timeout_seen = False
    for warning in warnings:
        text = _safe_warning(warning)
        if "消息源超过" in text or "超时" in text or "连接中断" in text:
            if timeout_seen:
                continue
            text = "部分消息源超时或连接中断，已降级使用其它来源"
            timeout_seen = True
        if text and text not in displayed:
            displayed.append(text)
        if len(displayed) >= limit:
            break
    return tuple(displayed)


def _short_text(value: object, max_chars: int) -> str:
    text = _inline(value)
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _verification_hint(event: CatalystEvent) -> str:
    if event.category == "外部冲击":
        return "先找外交部、财政部、海外监管或权威媒体原文，再判断影响链条。"
    if event.impact == "negative":
        return "先找公告/监管/交易所原文；若属实，把它当风险而不是博弈点。"
    if event.category in {"涨价/供需催化", "供给收缩"}:
        return "看是否有报价单、产业媒体二次确认，以及同链条股票是否一起放量。"
    if event.category in {"订单/需求验证", "业绩催化"}:
        return "优先找公司公告或权威媒体原文，再看开盘承接是否强于大盘。"
    if event.category == "政策催化":
        return "先确认政策原文和受益环节，不把泛概念当成直接利好。"
    return "先找原始来源，再看板块扩散和量价确认。"


def _frame_warnings(df: pd.DataFrame, *, prefix: str) -> list[str]:
    warnings = (
        getattr(df, "attrs", {}).get("aqsp_warnings", ()) if df is not None else ()
    )
    return [f"{prefix}: {warning}" for warning in warnings]


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
) -> tuple[pd.DataFrame, str]:
    try:
        return _call_fetcher_with_timeout(fetch, timeout_seconds=timeout_seconds), ""
    except Exception as exc:
        return pd.DataFrame(), str(exc)


def _akshare_symbol_news(symbol: str, limit: int) -> pd.DataFrame:
    import akshare as ak

    frames: list[pd.DataFrame] = []
    fetchers = (
        lambda: ak.stock_news_em(symbol=symbol),
        lambda: ak.stock_individual_notice_report(symbol=symbol),
        lambda: ak.stock_research_report_em(symbol=symbol),
    )
    warnings: list[str] = []
    for fetch in fetchers:
        df, warning = _fetch_optional_frame(fetch, timeout_seconds=6.0)
        if warning:
            warnings.append(warning)
        if df is not None and not df.empty:
            frames.append(df.head(limit))
    if not frames:
        empty = pd.DataFrame()
        empty.attrs["aqsp_warnings"] = tuple(warnings)
        return empty
    result = pd.concat(frames, ignore_index=True).head(limit * 3)
    result.attrs["aqsp_warnings"] = tuple(warnings[:3])
    return result


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
    warnings: list[str] = []
    per_source_limit = max(2, min(5, limit))
    for fetch in fetchers:
        df, warning = _fetch_optional_frame(fetch, timeout_seconds=6.0)
        if warning:
            warnings.append(warning)
        if df is not None and not df.empty:
            frames.append(df.head(per_source_limit))
    if not frames:
        empty = pd.DataFrame()
        empty.attrs["aqsp_warnings"] = tuple(warnings)
        return empty
    result = _prioritize_news_frame(pd.concat(frames, ignore_index=True)).head(limit)
    result.attrs["aqsp_warnings"] = tuple(warnings[:3])
    return result


def _prioritize_news_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    def priority(row: dict[str, Any]) -> int:
        source_text = _first_text(
            row, ("文章来源", "媒体", "source", "来源", "公告类型")
        )
        title = _first_text(
            row, ("新闻标题", "公告标题", "标题", "title", "内容", "摘要")
        )
        blob = f"{source_text} {title}"
        if any(token in blob for token in _AUTHORITATIVE_SOURCE_TOKENS):
            return 0
        if any(
            token in blob for token in ("新华社", "央视", "国常会", "发改委", "工信部")
        ):
            return 1
        if any(token in blob for token in _MEDIA_SOURCE_TOKENS):
            return 2
        return 3

    rows = df.to_dict(orient="records")
    ordered = sorted(enumerate(rows), key=lambda item: (priority(item[1]), item[0]))
    return pd.DataFrame([row for _, row in ordered])
