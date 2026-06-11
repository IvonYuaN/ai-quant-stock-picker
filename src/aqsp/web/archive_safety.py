"""Presentation-only wording guards for archived dashboard reports."""

from __future__ import annotations

import re

from aqsp.presentation import normalize_research_tone


ARCHIVE_ACTION_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("立即买入", "历史记录: 纸面观察"),
    ("首选下单", "历史记录: 重点复核"),
    ("执行开仓", "历史记录: 纸面推进"),
    ("执行顺序", "历史复核顺序"),
    ("执行名单", "历史复核名单"),
    ("重点跟踪线索", "历史复核线索"),
    ("跟踪优先级", "历史复核顺位"),
    ("重点跟踪对象", "历史复核对象"),
    ("重点跟踪名单", "历史复核名单"),
    ("今日重点名单", "历史重点记录"),
    ("重点跟踪", "历史复核"),
    ("纸面复核优先级", "历史复核顺位"),
    ("纸面复核主链", "历史主链复核"),
    ("纸面主链复核", "历史主链复核"),
    ("纸面复核对象", "历史复核对象"),
    ("纸面复核名单", "历史复核名单"),
    ("配仓执行", "历史仓位记录"),
    ("配仓建议", "历史仓位参考"),
    ("仓位建议", "历史仓位参考"),
    ("参考仓位执行", "历史仓位参考"),
    ("真实持仓", "历史纸面持有"),
    ("首选标的", "历史重点对象"),
    ("首选观察", "历史重点观察"),
    ("买入计划", "历史入场计划"),
    ("新开仓", "历史纸面新建观察"),
    ("开仓", "历史纸面观察"),
    ("平仓", "历史纸面退出记录"),
    ("买入", "历史纸面入场记录"),
    ("卖出", "历史纸面退出记录"),
    ("参考买点", "历史参考价"),
    ("买点", "历史参考价"),
    ("止盈", "历史观察目标"),
    ("止损", "历史防守位"),
    ("下单", "历史纸面记录"),
    ("今日建议", "历史回看"),
)


def sanitize_archive_text(text: str) -> str:
    """Neutralize archived action words without rewriting source files."""
    sanitized = text
    sanitized = re.sub(
        r"\bBUY\b\s*[\d,.]*(?:\s*@\s*[\d,.]+)?",
        "纸面入场记录",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\bSELL\b\s*[\d,.]*(?:\s*@\s*[\d,.]+)?",
        "纸面退出记录",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(r"(?<![不无])可执行主链", "纸面主链复核", sanitized)
    sanitized = re.sub(r"(?<![不无])可执行标的", "纸面复核对象", sanitized)
    sanitized = re.sub(r"(?<![不无])可执行", "纸面复核", sanitized)
    for source, replacement in ARCHIVE_ACTION_REPLACEMENTS:
        sanitized = sanitized.replace(source, replacement)
    return sanitized


def sanitize_archive_lines(lines: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sanitize_archive_text(line) for line in lines if line)


def sanitize_research_text(text: str) -> str:
    """Neutralize current research report lines without turning them into archive labels."""
    sanitized = normalize_research_tone(text)
    sanitized = re.sub(r"(?<![不无])可执行", "纸面复核", sanitized)
    sanitized = re.sub(
        r"\bBUY\b\s*[\d,.]*(?:\s*@\s*[\d,.]+)?",
        "纸面入场记录",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\bSELL\b\s*[\d,.]*(?:\s*@\s*[\d,.]+)?",
        "纸面退出记录",
        sanitized,
        flags=re.IGNORECASE,
    )
    replacements = (
        ("立即买入", "纸面重点观察"),
        ("首选下单", "重点复核"),
        ("执行开仓", "纸面推进"),
        ("执行顺序", "复核顺序"),
        ("配仓执行", "纸面配仓记录"),
        ("配仓建议", "纸面配仓参考"),
        ("仓位建议", "纸面仓位参考"),
        ("参考仓位执行", "纸面仓位参考"),
        ("真实持仓", "纸面持有"),
        ("首选标的", "纸面重点对象"),
        ("首选观察", "重点观察"),
        ("新开仓", "纸面新建观察"),
        ("开仓", "纸面观察"),
        ("平仓", "纸面退出记录"),
        ("买入", "纸面入场记录"),
        ("卖出", "纸面退出记录"),
        ("参考买点", "参考价"),
        ("买点", "参考价"),
        ("止盈", "观察目标"),
        ("止损", "防守位"),
        ("下单", "纸面记录"),
        ("今日建议", "研究回看"),
    )
    for source, replacement in replacements:
        sanitized = sanitized.replace(source, replacement)
    return sanitized


def sanitize_research_lines(lines: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sanitize_research_text(line) for line in lines if line)
