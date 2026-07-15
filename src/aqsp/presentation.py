from __future__ import annotations

import re


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


def display_section_title(title: str) -> str:
    clean = str(title).strip()
    labels = {
        "主链总览": "今日结论",
        "市场态势": "市场环境",
        "数据源状态": "数据情况",
        "研究吸收": "研究进展",
        "候选来龙去脉": "候选说明",
        "候选证据链": "候选说明",
        "题材热度": "线索分布",
        "明日重点": "明日先看",
    }
    return labels.get(clean, clean)


def source_freshness_label(value: str) -> str:
    clean = str(value).strip()
    labels = {
        "terminal_realtime": "终端级实时",
        "realtime": "盘中实时",
        "delayed_realtime": "延时盘中",
        "end_of_day": "收盘后",
        "historical_batch": "批量历史",
        "cached": "缓存历史",
        "point_in_time": "时点财务",
        "unknown": "未记录",
    }
    return labels.get(clean, clean or "未记录")


def source_coverage_label(value: str) -> str:
    clean = str(value).strip()
    labels = {
        "multi_dimensional": "多维行情",
        "quotes_plus": "增强行情",
        "broad_research": "研究扩展",
        "history_plus": "扩展历史",
        "history_core": "核心历史",
        "pit_fundamental": "时点财务",
        "warehouse": "仓库缓存",
        "execution_state": "执行状态",
        "unknown": "未记录",
    }
    return labels.get(clean, clean or "未记录")


def source_local_status_label(value: str) -> str:
    clean = str(value).strip()
    labels = {
        "present": "本地缓存可用",
        "missing": "本地缓存缺失",
        "stale": "本地缓存偏旧",
        "not_required": "无需本地缓存",
        "unknown": "未记录",
    }
    return labels.get(clean, clean or "未记录")


def source_health_label(value: str) -> str:
    clean = str(value).strip()
    labels = {
        "healthy": "正常",
        "fallback": "已切换备用源",
        "degraded": "需降低信任",
        "cold_start": "冷启动观察",
        "unknown": "未记录",
    }
    return labels.get(clean, clean or "未记录")


def format_source_route(requested: str, actual: str) -> str:
    requested_clean = str(requested).strip()
    actual_clean = str(actual).strip()
    if requested_clean and actual_clean and requested_clean != actual_clean:
        return f"{requested_clean} -> {actual_clean}"
    return actual_clean or requested_clean or "未记录"


def describe_source_layers(
    freshness: str,
    coverage: str,
    local_status: str = "",
) -> str:
    parts = [
        source_freshness_label(freshness),
        source_coverage_label(coverage),
    ]
    local_part = (
        source_local_status_label(local_status) if str(local_status).strip() else ""
    )
    if local_part:
        parts.append(local_part)
    return " / ".join(part for part in parts if part)


def describe_source_health(label: str, message: str) -> str:
    clean_message = str(message).strip()
    replacements = (
        ("fallback 到 ", "已切换到备用数据源 "),
        ("plan成功/失败", "计划成功/失败"),
        ("源成功/失败", "数据源成功/失败"),
        ("最近失败偏多", "最近失败较多"),
        ("暂无健康历史，处于冷启动观察期", "暂无足够历史，仍在冷启动观察期"),
    )
    for old, new in replacements:
        clean_message = clean_message.replace(old, new)
    clean_message = normalize_research_tone(clean_message)
    status = source_health_label(label)
    if not clean_message:
        return status
    return f"{status} / {clean_message}"


def humanize_runtime_snapshot_line(line: str) -> str:
    clean = str(line).strip()
    if not clean:
        return ""

    if clean.startswith("数据源:"):
        return "数据来源: " + clean.split(":", 1)[1].strip()

    if clean.startswith(("数据层级:", "数据完整度:")):
        raw = clean.split(":", 1)[1].strip()
        match = re.search(
            r"fresh=([^/]+)\s*/\s*cover=([^/]+)(?:\s*/\s*local=([^/]+))?",
            raw,
        )
        if match:
            return "数据完整度: " + describe_source_layers(
                match.group(1).strip(),
                match.group(2).strip(),
                (match.group(3) or "").strip(),
            )
        return "数据完整度: " + raw

    if clean.startswith("数据时效:"):
        raw = clean.split(":", 1)[1].strip()
        match = re.search(r"latest=([^/]+)\s*/\s*lag=(\d+)d", raw)
        if match:
            return (
                f"数据时效: 最新交易日 {match.group(1).strip()} / "
                f"延迟 {match.group(2).strip()} 天"
            )
        return "数据时效: " + raw

    if clean.startswith(("数据健康:", "数据状态:")):
        raw = clean.split(":", 1)[1].strip()
        parts = raw.split(" / ", 1)
        label = parts[0].strip()
        message = parts[1].strip() if len(parts) > 1 else ""
        return "数据状态: " + describe_source_health(label, message)

    if clean.startswith("候选池:"):
        return "扫描范围: " + clean.split(":", 1)[1].strip()

    if clean.startswith(("thresholds.version:", "规则版本:")):
        return "规则版本: " + clean.split(":", 1)[1].strip()

    if clean.startswith(("regime:", "市场标签:")):
        return "市场标签: " + clean.split(":", 1)[1].strip()

    return normalize_research_tone(clean)


def normalize_research_tone(text: str) -> str:
    """Keep user-visible text neutral without rewriting business meaning."""
    replacements = (
        ("AI 量化选股系统", "AQSP"),
        ("AI 量化选股日报", "每日研究复盘"),
        ("AI量化选股", "AQSP"),
        ("AI 研究", "研究"),
        ("ai 研究", "研究"),
        ("多Agent辩论偏谨慎", "分歧偏谨慎"),
        ("多Agent辩论支持上调优先级", "分歧支持提高优先级"),
        ("fallback 到 ", "已切换到备用数据源 "),
        ("Agent表现快照", "观点快照"),
        ("agent 观点", "观点"),
        ("agent 明细", "观点明细"),
        (" 个 agent", " 个观点"),
        ("多Agent辩论", "多 Agent 讨论"),
        ("PM主裁决", "结果概览"),
        ("PM 上调优先级", "优先级上调"),
        ("PM 降级观察", "优先级下调"),
        ("Portfolio Manager", "排序"),
        ("继续观察名单", "继续观察"),
        ("观察名单接下来", "后续关注"),
        ("观察名单复核", "后续关注"),
        ("现在卡在哪", "当前限制"),
        ("需要重点确认", "待确认"),
        ("比例参考", "仓位参考"),
        ("配置说明", "仓位约束"),
        ("再看顺序", "先看顺序"),
        ("模型复核", "复核"),
        ("PM依据", "调整原因"),
        ("决策依据", "判断原因"),
        ("研究依据", "研究记录"),
        ("缺少研究结论", "缺少结论"),
        ("研究侧存在阻塞", "存在阻塞"),
        ("研究侧待确认", "待确认"),
        ("研究结论已落盘", "已落盘"),
        ("研究动作", "跟踪状态"),
        ("研究下一步", "后续关注"),
        ("推荐依据", "候选原因"),
        ("调整依据", "调整原因"),
        ("复核依据", "复核记录"),
        ("原始依据", "原始记录"),
        ("待核对依据", "待核对原因"),
        ("待补依据", "待补原因"),
        ("依据", "原因"),
        ("重点跟踪名单", "纸面复核名单"),
        ("重点跟踪对象", "纸面复核对象"),
        ("重点跟踪", "纸面复核"),
        ("跟踪优先级", "纸面复核优先级"),
        ("runtime原始分", "系统原始评分"),
        ("runtime 原始分", "系统原始评分"),
        ("不直接覆盖 runtime 打分", "不改写系统评分"),
        ("不直接覆盖 系统评分", "不改写系统评分"),
        ("不覆盖runtime打分", "不改写系统评分"),
        ("不覆盖 runtime 打分", "不改写系统评分"),
        ("runtime 打分", "系统评分"),
        ("runtime", "运行"),
        ("Runtime", "运行"),
        ("thresholds.version", "规则版本"),
        ("regime:", "市场标签:"),
        ("真实持仓", "纸面持有"),
        ("转入执行名单", "转入纸面复核名单"),
        ("提升执行顺位", "提升纸面复核优先级"),
        ("执行顺位", "纸面复核优先级"),
        ("执行名单", "纸面复核名单"),
        ("执行约束", "纸面约束"),
        ("执行阻塞", "纸面阻塞"),
        ("维持原排序", "结果不变"),
        ("上调优先级", "优先级上调"),
        ("降级观察", "优先级下调"),
        ("新开仓", "纸面新建观察"),
        ("开仓", "纸面观察"),
        ("下单", "纸面记录"),
        ("参考买点", "参考价"),
        ("纸面参考价", "记录时价格"),
        ("防守线", "最多亏到"),
        ("买入", "纸面入场记录"),
        ("onerror", "removed_attr"),
        ("javascript:", "removed_url:"),
    )
    clean = str(text).strip()
    for old, new in replacements:
        clean = clean.replace(old, new)
    return clean
