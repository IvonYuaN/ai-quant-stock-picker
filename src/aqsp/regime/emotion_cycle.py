"""短线情绪周期模型 v1 —— phase 状态机 + 四维评分（纯逻辑层，无 I/O）。

为什么单独成模块
----------------
``backend/market.py::_emotion()`` 已经能拿到连板梯队 / 最高连板 / 炸板率 /
封板率 / 晋级率 / 涨跌停家数，但**只把它们平铺成字段**，没有「当前处于情绪周期
哪一段」的判断。本模块补的正是这层判断，并且刻意放在 ``src/aqsp/`` 而不是
``backend/``：

- ``backend/`` 依赖 ``astock``（生产机专有），本地无法 import ⇒ 逻辑放那儿就没法单测；
- 情绪判定是**纯计算**，按 AGENTS.md §4 不得在读磁盘/网络；
- 阈值放 ``config/emotion_model.yaml``（**不是** ``thresholds.yaml``：后者是选股
  阈值，改动必须走 walk-forward；本模型不参与选股打分）。

模型口径
--------
情绪周期 = 冰点 → 修复 → 发酵 → 高潮 → 退潮 → 冰点。判定分两步：

1. **四维评分**（0–100，只用于展示与解释，不直接决定 phase）：
   ``profit`` 赚钱效应 / ``loss`` 亏钱效应 / ``breadth`` 市场宽度 / ``focus`` 题材聚焦度。
   每个维度由若干指标经 ``[floor, ceil]`` 分段线性归一化后加权。
2. **phase 状态机**：按 ``config/emotion_model.yaml`` 的 ``phase_rules`` **自上而下
   first-match**。每条规则都必须能被一句话解释（``description`` 原样回传前端）。

🔴 三条纪律
-----------
- **fail-closed，不猜**：规则里任一指标为 ``None`` ⇒ 该规则不成立；整体无数据 ⇒
  phase = ``unknown``，绝不用默认值编一个「分歧」出来。
- **无魔法数字**：所有阈值来自 YAML；本模块只有 ``0/100`` 这类归一化端点常量。
- **可解释**：``EmotionAssessment.reasons`` 给出「命中哪条规则 + 关键指标实测值」，
  前端直接展示，不做事后归因。

v1 的已知边界（后续可扩展，不假装已实现）
------------------------------------------
- **不使用历史**：``repairing`` 靠「赚钱效应 40–55 且晋级率 ≥ 0.20」判定，
  而不是「昨日是冰点」。要加历史滞回需先有快照库，见 issue #394。
- **``breadth`` / ``focus`` 缺失时降级**：调用方拿不到涨跌家数或行业分布时，
  该维度为 ``None``，综合分在**剩余维度上重新归一化**，不按 0 分处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "emotion_model.yaml"
)

# 归一化端点（不是业务阈值，不需要进 YAML）。
SCORE_MIN = 0.0
SCORE_MAX = 100.0

PHASE_UNKNOWN = "unknown"
PHASE_UNKNOWN_LABEL = "数据不足"

# phase 名称 → 中文标签的兜底映射。YAML 里每条规则自带 label；
# 这里只为 unknown 与「配置缺 label」提供兜底。
_PHASE_LABELS = {
    PHASE_UNKNOWN: PHASE_UNKNOWN_LABEL,
    "freezing": "冰点",
    "repairing": "修复",
    "fermenting": "发酵",
    "climax": "高潮",
    "ebbing": "退潮",
    "diverging": "分歧",
}


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmotionSnapshot:
    """某个交易日的情绪原始观测（全部为客观计数/比率，不含任何预测）。"""

    trade_date: str = ""
    zt_count: int = 0
    dt_count: int = 0
    zb_count: int = 0
    max_boards: int = 0
    lianban_count: int = 0
    yzt_count: int = 0
    # 昨日涨停股今日平均涨跌幅（%）—— 赚钱效应最直接的观测
    yzt_feedback: float | None = None
    # 昨日涨停股今日上涨占比（0–1）
    yzt_up_ratio: float | None = None
    # 最高板梯队今日平均涨跌幅（%）—— 高标杀跌的观测
    high_board_pct: float | None = None
    seal_rate: float | None = None
    break_rate: float | None = None
    promotion_rate: float | None = None
    up_count: int = 0
    down_count: int = 0
    # (行业/概念, 占涨停家数比例)，降序；用于题材聚焦度
    theme_shares: tuple[tuple[str, float], ...] = ()

    def has_data(self) -> bool:
        """是否至少观测到涨跌停家数（否则一切评分都无意义）。"""
        return (self.zt_count + self.dt_count + self.zb_count) > 0


def derived_metrics(snapshot: EmotionSnapshot) -> dict[str, float | None]:
    """把快照折算成评分用的派生指标（缺失一律 ``None``，不填 0）。"""
    zt = snapshot.zt_count
    dt_pressure = (snapshot.dt_count / zt) if zt > 0 else None

    breadth_total = snapshot.up_count + snapshot.down_count
    up_ratio = (snapshot.up_count / breadth_total) if breadth_total > 0 else None

    shares = [float(share) for _, share in snapshot.theme_shares if share is not None]
    top_theme_share = max(shares) if shares else None
    hhi = sum(share * share for share in shares) if shares else None

    return {
        "yzt_feedback": snapshot.yzt_feedback,
        "yzt_up_ratio": snapshot.yzt_up_ratio,
        "seal_rate": snapshot.seal_rate,
        "promotion_rate": snapshot.promotion_rate,
        "break_rate": snapshot.break_rate,
        "dt_pressure": dt_pressure,
        "high_board_pct": snapshot.high_board_pct,
        "up_ratio": up_ratio,
        "top_theme_share": top_theme_share,
        "hhi": hhi,
    }


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricBand:
    """单个指标的归一化区间。``invert=True`` 表示数值越小越严重。"""

    floor: float
    ceil: float
    weight: float
    invert: bool = False


@dataclass(frozen=True)
class DimensionConfig:
    name: str
    label: str
    weight: float
    metrics: Mapping[str, MetricBand]


@dataclass(frozen=True)
class PhaseRule:
    name: str
    label: str
    description: str
    when: Mapping[str, float]


@dataclass(frozen=True)
class EmotionModelConfig:
    version: str
    effective_from: str
    description: str
    dimensions: Mapping[str, DimensionConfig]
    phase_rules: tuple[PhaseRule, ...]


def _parse_band(name: str, raw: Mapping[str, Any]) -> MetricBand:
    try:
        floor = float(raw["floor"])
        ceil = float(raw["ceil"])
        weight = float(raw.get("weight", 1.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"emotion_model: 指标 {name} 的 floor/ceil/weight 非法") from exc
    if ceil <= floor:
        raise ValueError(
            f"emotion_model: 指标 {name} 的 ceil({ceil}) 必须大于 floor({floor})；"
            "反向指标请用 invert: true"
        )
    if weight <= 0:
        raise ValueError(f"emotion_model: 指标 {name} 的 weight 必须为正")
    return MetricBand(
        floor=floor, ceil=ceil, weight=weight, invert=bool(raw.get("invert", False))
    )


def parse_emotion_model(payload: Mapping[str, Any]) -> EmotionModelConfig:
    """解析 YAML 字典 → 配置对象。配置错误一律抛 ``ValueError``（启动期就该炸）。"""
    raw_dimensions = payload.get("dimensions")
    if not isinstance(raw_dimensions, Mapping) or not raw_dimensions:
        raise ValueError("emotion_model: 缺少 dimensions")

    dimensions: dict[str, DimensionConfig] = {}
    for name, raw_dim in raw_dimensions.items():
        if not isinstance(raw_dim, Mapping):
            raise ValueError(f"emotion_model: 维度 {name} 必须是映射")
        raw_metrics = raw_dim.get("metrics")
        if not isinstance(raw_metrics, Mapping) or not raw_metrics:
            raise ValueError(f"emotion_model: 维度 {name} 缺少 metrics")
        dimensions[str(name)] = DimensionConfig(
            name=str(name),
            label=str(raw_dim.get("label", name)),
            weight=float(raw_dim.get("weight", 1.0)),
            metrics={
                str(metric): _parse_band(str(metric), band)
                for metric, band in raw_metrics.items()
            },
        )

    raw_rules = payload.get("phase_rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("emotion_model: 缺少 phase_rules")
    rules: list[PhaseRule] = []
    for index, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, Mapping):
            raise ValueError(f"emotion_model: phase_rules[{index}] 必须是映射")
        name = str(raw_rule.get("name") or "").strip()
        if not name:
            raise ValueError(f"emotion_model: phase_rules[{index}] 缺少 name")
        when = raw_rule.get("when") or {}
        if not isinstance(when, Mapping):
            raise ValueError(f"emotion_model: 规则 {name} 的 when 必须是映射")
        # 启动期就校验条件键：写错一个键（如 profit_at_leat）会让规则**永不命中**
        # 且毫无报错，属于典型静默失效。宁可启动就炸。
        unknown_keys = [str(k) for k in when if str(k) not in _CONDITION_KEYS]
        if unknown_keys:
            raise ValueError(
                f"emotion_model: 规则 {name} 含未知条件 {unknown_keys}；"
                f"可用条件见 _CONDITION_KEYS：{sorted(_CONDITION_KEYS)}"
            )
        rules.append(
            PhaseRule(
                name=name,
                label=str(raw_rule.get("label") or _PHASE_LABELS.get(name, name)),
                description=str(raw_rule.get("description") or ""),
                when={str(k): float(v) for k, v in when.items()},
            )
        )
    if rules[-1].when:
        raise ValueError(
            "emotion_model: 最后一条 phase_rules 必须是兜底规则（when 为空），"
            "否则会有观测组合无 phase 可归"
        )

    return EmotionModelConfig(
        version=str(payload.get("version", "")),
        effective_from=str(payload.get("effective_from", "")),
        description=str(payload.get("description", "")),
        dimensions=dimensions,
        phase_rules=tuple(rules),
    )


def load_emotion_model(path: str | Path | None = None) -> EmotionModelConfig:
    """读 ``config/emotion_model.yaml``。文件缺失/非法 ⇒ ``ValueError``。"""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"emotion_model 配置不可读: {target}") from exc
    payload = yaml.safe_load(text)
    if not isinstance(payload, Mapping):
        raise ValueError(f"emotion_model 配置顶层必须是映射: {target}")
    return parse_emotion_model(payload)


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------


def normalize_metric(value: float | None, band: MetricBand) -> float | None:
    """把指标值按 ``[floor, ceil]`` 线性映射到 0–100（超界截断）。``None`` 透传。"""
    if value is None:
        return None
    span = band.ceil - band.floor
    ratio = (float(value) - band.floor) / span
    clamped = min(max(ratio, 0.0), 1.0)
    score = SCORE_MIN + (SCORE_MAX - SCORE_MIN) * clamped
    return SCORE_MAX - score if band.invert else score


@dataclass(frozen=True)
class DimensionScore:
    name: str
    label: str
    score: float | None
    weight: float
    metrics: Mapping[str, float | None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "score": None if self.score is None else round(self.score, 1),
            "weight": self.weight,
            "metrics": {
                key: (None if value is None else round(value, 1))
                for key, value in self.metrics.items()
            },
        }


def score_dimensions(
    snapshot: EmotionSnapshot, config: EmotionModelConfig
) -> dict[str, DimensionScore]:
    """四维评分。某维度一个指标都拿不到 ⇒ ``score=None``（不按 0 分处理）。"""
    derived = derived_metrics(snapshot)
    out: dict[str, DimensionScore] = {}
    for name, dim in config.dimensions.items():
        metric_scores: dict[str, float | None] = {}
        weighted_sum = 0.0
        weight_sum = 0.0
        for metric_name, band in dim.metrics.items():
            score = normalize_metric(derived.get(metric_name), band)
            metric_scores[metric_name] = score
            if score is not None:
                weighted_sum += score * band.weight
                weight_sum += band.weight
        out[name] = DimensionScore(
            name=name,
            label=dim.label,
            score=(weighted_sum / weight_sum) if weight_sum > 0 else None,
            weight=dim.weight,
            metrics=metric_scores,
        )
    return out


def composite_score(
    scores: Mapping[str, DimensionScore], config: EmotionModelConfig
) -> float | None:
    """综合分：只在**有值**的维度上重新归一化权重（缺维度 ≠ 0 分）。"""
    weighted_sum = 0.0
    weight_sum = 0.0
    for name, dim in config.dimensions.items():
        score = scores.get(name)
        if score is None or score.score is None:
            continue
        weighted_sum += score.score * dim.weight
        weight_sum += dim.weight
    return (weighted_sum / weight_sum) if weight_sum > 0 else None


# ---------------------------------------------------------------------------
# phase 状态机
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseVerdict:
    name: str
    label: str
    description: str
    rule_index: int | None
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "rule_index": self.rule_index,
            "reasons": list(self.reasons),
        }


def _rule_context(
    snapshot: EmotionSnapshot,
    scores: Mapping[str, DimensionScore],
    derived: Mapping[str, float | None],
) -> dict[str, float | None]:
    """把规则可能引用的量摊平成一张表，供 ``when`` 逐个比对。"""
    profit = scores["profit"].score if "profit" in scores else None
    loss = scores["loss"].score if "loss" in scores else None
    return {
        "profit": profit,
        "loss": loss,
        "zt_count": float(snapshot.zt_count),
        "max_boards": float(snapshot.max_boards),
        "promotion_rate": derived.get("promotion_rate"),
        "break_rate": derived.get("break_rate"),
        "dt_pressure": derived.get("dt_pressure"),
        "lianban_count": float(snapshot.lianban_count),
    }


# ``when`` 的键 → (上下文里的量, 比较方式)。新增比较方式必须同时改这里与
# ``_compare``，避免「YAML 写了但代码不认」的静默失效。
_CONDITION_KEYS = {
    "profit_at_least": ("profit", "at_least"),
    "profit_at_most": ("profit", "at_most"),
    "profit_below": ("profit", "below"),
    "loss_at_least": ("loss", "at_least"),
    "zt_count_at_least": ("zt_count", "at_least"),
    "max_boards_at_least": ("max_boards", "at_least"),
    "promotion_rate_at_least": ("promotion_rate", "at_least"),
    "break_rate_at_least": ("break_rate", "at_least"),
    "dt_pressure_at_least": ("dt_pressure", "at_least"),
    "lianban_count_at_least": ("lianban_count", "at_least"),
}


def _compare(value: float | None, threshold: float, mode: str) -> bool:
    """任一操作数为 ``None`` ⇒ 条件不成立（fail-closed，绝不把缺失当 0）。"""
    if value is None:
        return False
    if mode == "at_least":
        return value >= threshold
    if mode == "at_most":
        return value <= threshold
    if mode == "below":
        return value < threshold
    raise ValueError(f"emotion_cycle: 未知比较方式 {mode}")


def classify_phase(
    snapshot: EmotionSnapshot,
    config: EmotionModelConfig,
    *,
    scores: Mapping[str, DimensionScore] | None = None,
) -> PhaseVerdict:
    """按 YAML 的 ``phase_rules`` 自上而下 first-match 定 phase。

    无任何涨跌停观测 ⇒ ``unknown``（数据不足），不编一个默认 phase。
    """
    if not snapshot.has_data():
        return PhaseVerdict(
            name=PHASE_UNKNOWN,
            label=PHASE_UNKNOWN_LABEL,
            description="当日无涨停/跌停/炸板观测，无法判定情绪周期",
            rule_index=None,
        )

    dimension_scores = scores if scores is not None else score_dimensions(snapshot, config)
    context = _rule_context(snapshot, dimension_scores, derived_metrics(snapshot))

    for index, rule in enumerate(config.phase_rules):
        if not rule.when:
            return PhaseVerdict(
                name=rule.name,
                label=rule.label,
                description=rule.description,
                rule_index=index,
                reasons=(f"兜底规则：{rule.description}",),
            )
        reasons: list[str] = []
        matched = True
        for key, threshold in rule.when.items():
            target = _CONDITION_KEYS.get(key)
            if target is None:
                raise ValueError(f"emotion_cycle: 未知规则条件 {key}")
            metric_name, mode = target
            value = context.get(metric_name)
            if not _compare(value, threshold, mode):
                matched = False
                break
            reasons.append(f"{metric_name}={_fmt(value)} 满足 {key}={_fmt(threshold)}")
        if matched:
            return PhaseVerdict(
                name=rule.name,
                label=rule.label,
                description=rule.description,
                rule_index=index,
                reasons=tuple(reasons),
            )

    # parse_emotion_model 已强制最后一条是兜底规则，理论上到不了这里。
    return PhaseVerdict(
        name=PHASE_UNKNOWN,
        label=PHASE_UNKNOWN_LABEL,
        description="没有命中任何 phase 规则（配置缺少兜底规则）",
        rule_index=None,
    )


def _fmt(value: float | None) -> str:
    if value is None:
        return "缺失"
    return f"{value:.2f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# 对外总入口
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmotionAssessment:
    """一次情绪评估的完整结论（可直接 json 化后并进 API 响应）。"""

    trade_date: str
    phase: PhaseVerdict
    composite: float | None
    dimensions: Mapping[str, DimensionScore]
    config_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "phase": self.phase.as_dict(),
            "composite": None if self.composite is None else round(self.composite, 1),
            "dimensions": {
                name: score.as_dict() for name, score in self.dimensions.items()
            },
            "model_version": self.config_version,
        }


def assess(
    snapshot: EmotionSnapshot,
    config: EmotionModelConfig | None = None,
) -> EmotionAssessment:
    """四维评分 + phase 判定。``config=None`` 时读默认 YAML。"""
    model = config if config is not None else load_emotion_model()
    scores = score_dimensions(snapshot, model)
    return EmotionAssessment(
        trade_date=snapshot.trade_date,
        phase=classify_phase(snapshot, model, scores=scores),
        composite=composite_score(scores, model),
        dimensions=scores,
        config_version=model.version,
    )


def theme_shares_from_industries(industries: Iterable[str]) -> tuple[tuple[str, float], ...]:
    """把涨停股的行业/概念标签折成「占比降序」的题材分布（供 ``focus`` 维度）。

    空标签（东财偶尔给空串）不参与统计；结果四舍五入到 4 位，便于前端展示与快照比对。
    """
    counts: dict[str, int] = {}
    total = 0
    for raw in industries:
        name = str(raw or "").strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
        total += 1
    if total == 0:
        return ()
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple((name, round(count / total, 4)) for name, count in ranked)


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DimensionConfig",
    "DimensionScore",
    "EmotionAssessment",
    "EmotionModelConfig",
    "EmotionSnapshot",
    "MetricBand",
    "PHASE_UNKNOWN",
    "PHASE_UNKNOWN_LABEL",
    "PhaseRule",
    "PhaseVerdict",
    "assess",
    "classify_phase",
    "composite_score",
    "derived_metrics",
    "load_emotion_model",
    "normalize_metric",
    "parse_emotion_model",
    "score_dimensions",
    "theme_shares_from_industries",
]
