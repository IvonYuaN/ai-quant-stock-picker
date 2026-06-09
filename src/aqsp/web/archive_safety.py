"""Presentation-only wording guards for archived dashboard reports."""

from __future__ import annotations

import re


ARCHIVE_ACTION_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("立即买入", "历史记录: 纸面观察"),
    ("首选下单", "历史记录: 重点复核"),
    ("执行开仓", "历史记录: 纸面推进"),
    ("执行顺序", "历史复核顺序"),
    ("执行名单", "历史复核名单"),
    ("配仓执行", "纸面配仓记录"),
    ("配仓建议", "纸面配仓参考"),
    ("仓位建议", "纸面仓位参考"),
    ("参考仓位执行", "纸面仓位参考"),
    ("首选标的", "纸面重点对象"),
    ("首选观察", "重点观察"),
    ("新开仓", "纸面新建观察"),
    ("参考买点", "参考价"),
    ("买点", "参考价"),
    ("止盈", "观察目标"),
    ("止损", "防守位"),
    ("下单", "纸面记录"),
    ("今日建议", "历史回看"),
)


def sanitize_archive_text(text: str) -> str:
    """Neutralize archived action words without rewriting source files."""
    sanitized = text
    sanitized = re.sub(r"(?<![不无])可执行主链", "纸面主链复核", sanitized)
    sanitized = re.sub(r"(?<![不无])可执行标的", "纸面复核对象", sanitized)
    sanitized = re.sub(r"(?<![不无])可执行", "纸面复核", sanitized)
    for source, replacement in ARCHIVE_ACTION_REPLACEMENTS:
        sanitized = sanitized.replace(source, replacement)
    return sanitized


def sanitize_archive_lines(lines: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sanitize_archive_text(line) for line in lines if line)
