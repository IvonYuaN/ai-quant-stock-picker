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
    """Normalize user-visible wording to paper-research language."""
    replacements = (
        ("多Agent辩论", "多视角讨论"),
        ("多 Agent", "多视角"),
        ("PM纸面裁决", "今日处理"),
        ("PM主裁决", "今日结论"),
        ("PM依据", "调整原因"),
        ("主链摘要", "一眼结论"),
        ("主链总览", "今日结论"),
        ("市场态势", "市场环境"),
        ("数据源状态", "数据情况"),
        ("候选来龙去脉", "候选说明"),
        ("候选证据链", "候选说明"),
        ("题材热度", "线索分布"),
        ("明日重点", "明日先看"),
        ("纸面复核主链", "今日重点名单"),
        ("主链候选", "今日重点名单"),
        ("候选观察池", "继续观察名单"),
        ("观察池", "继续观察名单"),
        ("裁决热点", "需要重点确认"),
        ("辩论速览", "讨论速览"),
        ("辩论结论", "讨论结论"),
        ("辩论倾向", "讨论倾向"),
        ("辩论分歧", "讨论分歧"),
        ("研究吸收", "研究进展"),
        ("研究雷达", "研究跟踪"),
        ("研究发现落盘", "研究结论落地情况"),
        ("已吸收但未直接入分策略族", "已纳入观察但未直接计分的策略"),
        ("已部分实现策略族", "已部分接入的策略"),
        ("report-only 研究族", "仅报告展示的研究"),
        ("运行门控研究族", "仍在验证中的研究"),
        ("下一接入重点", "下一步补充研究"),
        ("当前前置缺口", "当前缺少条件"),
        ("策略主配比", "现在偏向"),
        ("策略配比", "现在偏向"),
        ("当前侧重策略", "现在偏向"),
        ("当前优先策略", "更偏好这些方向"),
        ("优先策略", "更偏好这些方向"),
        ("优先关注策略", "更偏好这些方向"),
        ("策略权重建议", "方向占比参考"),
        ("策略权重参考", "方向占比参考"),
        ("纸面组合配置参考", "比例参考"),
        ("纸面组合参考", "比例参考"),
        ("纸面配仓参考", "比例参考"),
        ("仓位参考", "比例参考"),
        ("纸面约束", "跟踪约束"),
        ("纸面阻塞", "现在卡在哪"),
        ("当前卡点", "现在卡在哪"),
        ("纸面重点复核", "重点跟踪"),
        ("纸面复核名单", "重点跟踪名单"),
        ("纸面复核优先级", "跟踪优先级"),
        ("纸面复核对象", "重点跟踪对象"),
        ("纸面复核", "重点跟踪"),
        ("观察候选", "继续观察"),
        ("首位候选", "先看这个"),
        ("首选观察", "先看这个"),
        ("首先关注", "先看这个"),
        ("观察复核", "观察名单接下来"),
        ("Top ", "重点 "),
        ("runtime原始分", "系统原始评分"),
        ("runtime 原始分", "系统原始评分"),
        ("不直接覆盖 runtime 打分", "不改写系统评分"),
        ("不直接覆盖 系统评分", "不改写系统评分"),
        ("不覆盖runtime打分", "不改写系统评分"),
        ("不覆盖 runtime 打分", "不改写系统评分"),
        ("runtime 打分", "系统评分"),
        ("thresholds.version", "规则版本"),
        ("regime:", "市场标签:"),
        ("立即买入", "纸面重点观察"),
        ("首选下单", "优先再看"),
        ("执行开仓", "纸面推进"),
        ("真实持仓", "纸面持有"),
        ("转入执行名单", "转入纸面复核名单"),
        ("提升执行顺位", "提升纸面复核优先级"),
        ("执行顺位", "纸面复核优先级"),
        ("执行名单", "纸面复核名单"),
        ("执行约束", "纸面约束"),
        ("执行阻塞", "纸面阻塞"),
        ("执行摘要", "回看摘要"),
        ("可执行主链", "纸面再看主线"),
        ("可执行标的", "纸面再看对象"),
        ("首选标的", "优先再看对象"),
        ("新开仓", "纸面新建观察"),
        ("开仓", "纸面观察"),
        ("下单", "纸面记录"),
        ("优先复核", "优先再看"),
        ("复核节奏", "再看时间"),
        ("备选观察名单", "继续观察名单"),
        ("多视角讨论", "不同看法"),
        ("参考买点", "参考价"),
        ("纸面参考价", "记录时价格"),
        ("防守线", "最多亏到"),
        ("观察目标", "先看目标"),
        ("买点", "观察参考"),
        ("买入", "纸面入场记录"),
        ("onerror", "removed_attr"),
        ("javascript:", "removed_url:"),
    )
    clean = str(text).strip()
    for old, new in replacements:
        clean = clean.replace(old, new)
    return clean
