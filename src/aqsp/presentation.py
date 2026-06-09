from __future__ import annotations


def review_priority_label(priority: str) -> str:
    clean = str(priority).strip()
    labels = {"high": "高优先级", "medium": "中优先级", "low": "低优先级"}
    return labels.get(clean, clean)


def format_review_meta(priority: str, review_window: str) -> str:
    parts = (
        review_priority_label(priority),
        str(review_window).strip(),
    )
    return " / ".join(part for part in parts if part)


def format_watch_review_line(
    display: str,
    *,
    priority: str = "",
    review_window: str = "",
    next_step: str = "",
) -> str:
    line = str(display).strip()
    meta = format_review_meta(priority, review_window)
    if meta:
        line += f" | {meta}"
    clean_next_step = normalize_research_tone(next_step)
    if clean_next_step:
        line += f" | {clean_next_step}"
    return line


def format_watch_review_action(
    display: str,
    *,
    priority: str = "",
    review_window: str = "",
    next_step: str = "",
    prefix: str = "先盯",
) -> str:
    line = f"{str(prefix).strip()} {str(display).strip()}".strip()
    clean_next_step = normalize_research_tone(next_step)
    if clean_next_step:
        line += f"，{clean_next_step}"
    meta = format_review_meta(priority, review_window)
    if meta:
        line += f"（{meta}）"
    return line + "。"


def has_meaningful_name(symbol: str, name: str) -> bool:
    clean_symbol = str(symbol).strip()
    clean_name = str(name).strip()
    return bool(clean_name and clean_name != clean_symbol)


def format_symbol_name(symbol: str, name: str) -> str:
    clean_symbol = str(symbol).strip()
    clean_name = str(name).strip()
    if not has_meaningful_name(clean_symbol, clean_name):
        return clean_symbol
    return f"{clean_symbol} {clean_name}"


def normalize_research_tone(text: str) -> str:
    """Normalize user-visible wording to paper-research language."""
    replacements = (
        ("转入执行名单", "转入纸面复核名单"),
        ("提升执行顺位", "提升纸面复核优先级"),
        ("执行顺位", "纸面复核优先级"),
        ("执行名单", "纸面复核名单"),
        ("执行约束", "纸面约束"),
        ("执行阻塞", "纸面阻塞"),
        ("可执行主链", "纸面复核主链"),
        ("可执行标的", "纸面复核对象"),
    )
    clean = str(text).strip()
    for old, new in replacements:
        clean = clean.replace(old, new)
    return clean
