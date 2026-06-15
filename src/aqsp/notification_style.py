from __future__ import annotations

import re

_SECTION_RENAMES = {
    "## 一眼看懂": "## 结论",
    "## 🧭 一眼看懂": "## 结论",
    "## 一眼结论": "## 结论",
    "## 核心结论": "## 结论",
    "## 今天先看": "## 重点",
    "## 🧭 今天先看": "## 重点",
    "## 主链候选": "## 候选",
    "## 📋 主链候选": "## 候选",
    "## 候选一览": "## 候选",
    "## 📋 候选一览": "## 候选",
    "## 风险先看": "## 风险",
    "## ⚠️ 风险先看": "## 风险",
    "## 风险与分歧": "## 风险",
    "## 🔒 风险与分歧": "## 风险",
    "## 风险/阻塞": "## 风险",
    "## 🔒 风险/阻塞": "## 风险",
    "## 明日复核": "## 明日",
    "## ✅ 明日复核": "## 明日",
    "## 接下来怎么做": "## 明日",
    "## ✅ 接下来怎么做": "## 明日",
    "## 下一步": "## 明日",
    "## ✅ 下一步": "## 明日",
    "## 今日快照": "## 快照",
    "## 📌 今日快照": "## 快照",
    "## 阅读顺序": "## 顺序",
    "## 🧭 阅读顺序": "## 顺序",
    "## 纸面仓位": "## 纸面",
    "## 📦 纸面仓位": "## 纸面",
    "## 不同看法": "## 分歧",
    "## 🗣️ 不同看法": "## 分歧",
    "## 研究补充": "## 研究",
    "## 🔬 研究补充": "## 研究",
    "## 明天先看": "## 明日",
    "## 📅 明天先看": "## 明日",
    "## 变化与复盘": "## 变化",
    "## 📈 变化与复盘": "## 变化",
    "## 运行侧写": "## 运行",
    "## 🧾 运行侧写": "## 运行",
    "## 处理清单": "## 处理",
    "## 告警回放": "## 告警",
    "## 详细告警": "## 告警",
}

_EMOJI_TRANSLATION = str.maketrans(
    {
        "🧭": "",
        "📋": "",
        "⚠": "",
        "️": "",
        "✅": "",
        "🔒": "",
        "📦": "",
        "🗣": "",
        "🔬": "",
        "📅": "",
        "📌": "",
        "📈": "",
        "🧾": "",
        "🎯": "",
        "🌦": "",
        "⭐": "",
        "🧪": "",
        "👀": "",
        "⏸": "",
        "🛡": "",
        "🔍": "",
        "📚": "",
        "🟡": "",
        "🔴": "",
        "🟢": "",
        "💧": "",
        "📝": "",
        "🚨": "",
        "⛔": "",
        "🆕": "",
        "🔄": "",
    }
)

_DROP_PREFIXES = (
    "> 阅读方式",
    "> 本通知",
    "- 边界提醒:",
    "- 不要做:",
    "- 今天先别做:",
)

_DROP_CONTAINS = (
    "不是交易指令",
    "不构成交易指令",
    "阅读方式",
    "不要",
    "怎么验证",
    "模型复核",
    "降级判断",
    "助手",
    "不直接替代人工判断",
)


def compact_notification_markdown(markdown: str, *, max_section_items: int = 6) -> str:
    lines = [_normalize_line(line) for line in str(markdown or "").splitlines()]
    lines = _drop_noise_lines(lines)
    lines = _collapse_blank_lines(lines)
    lines = _limit_section_items(lines, max_items=max_section_items)
    return "\n".join(lines).strip()


def _normalize_line(line: str) -> str:
    clean = line.rstrip().translate(_EMOJI_TRANSLATION)
    clean = clean.replace("｜", "|")
    clean = re.sub(r"\*\*([^*：:]{1,18})\*\*[：:]", r"- \1: ", clean)
    clean = re.sub(r"^\s*[-*]\s+\*\*([^*：:]{1,18})\*\*[：:]", r"- \1: ", clean)
    clean = re.sub(r"^\s*[-*]\s+[-*]\s+", "- ", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    return _SECTION_RENAMES.get(clean, clean)


def _drop_noise_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        clean = line.strip()
        if any(clean.startswith(prefix) for prefix in _DROP_PREFIXES):
            continue
        if any(token in clean for token in _DROP_CONTAINS):
            continue
        out.append(line)
    return out


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                out.append("")
            blank = True
            continue
        out.append(line)
        blank = False
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return out


def _limit_section_items(lines: list[str], *, max_items: int) -> list[str]:
    out: list[str] = []
    section_items = 0
    skipped = 0
    current_limit = max_items
    for line in lines:
        if line.startswith("## "):
            if skipped:
                out.append(f"- 其余 {skipped} 条见完整报告")
            section_items = 0
            skipped = 0
            current_limit = 12 if line == "## 结论" else max_items
            out.append(line)
            continue
        if line.startswith("# "):
            out.append(line)
            continue
        if line.startswith("- ") or re.match(r"^\d+\.\s", line):
            section_items += 1
            if section_items > current_limit:
                skipped += 1
                continue
        out.append(line)
    if skipped:
        out.append(f"- 其余 {skipped} 条见完整报告")
    return out
