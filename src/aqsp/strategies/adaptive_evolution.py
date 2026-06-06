"""智能自进化机制 - 4层自适应系统。

进化层次：
- Layer 1: 参数微调（已有 auto_evolution.py，本模块不重复）
- Layer 2: 因子动态权重（监测IC衰减，淘汰失效因子）
- Layer 3: 策略组合自适应（市场制度联动）
- Layer 4: 元策略学习（根据近期表现自动路由）

设计原则：
- 不修改 thresholds.yaml（红线）
- 在内存中维护 evolution_state.json
- 任何变化都有日志和回滚机制
- 通过 walk-forward 双门验证才能正式生效
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from aqsp.core.time import now_shanghai, today_shanghai


# ============================================================
# Layer 2: 因子动态权重
# ============================================================

@dataclass(frozen=True)
class FactorPerformance:
    """单因子近期表现。"""

    factor_name: str
    ic_30d: float  # 信息系数（30日滚动）
    ic_decay: float  # IC衰减率
    win_rate_30d: float  # 30日胜率
    sharpe_30d: float  # 30日夏普
    sample_count: int
    last_updated: date


@dataclass(frozen=True)
class FactorWeightAdjustment:
    """因子权重调整建议。"""

    factor_name: str
    current_weight: float
    suggested_weight: float
    adjustment_reason: str
    confidence: float


class FactorWeightAdaptor:
    """因子动态权重调整器。

    监控每个因子的：
    - IC（信息系数）：因子值与未来收益的相关性
    - IC 衰减率：IC 是否在恶化
    - 胜率：因子选股的胜率
    - Sharpe：风险调整后收益

    规则：
    - IC < 0.05 30天 → 降权50%
    - IC < 0 30天 → 停用
    - IC > 0.15 30天 → 加权20%
    - 新因子 → 给低初始权重，逐步验证
    """

    IC_DEGRADED = 0.05  # IC 阈值
    IC_DEAD = 0.0  # IC 死亡阈值
    IC_STRONG = 0.15  # 强因子阈值
    MIN_WEIGHT = 0.05  # 最低权重
    MAX_WEIGHT = 0.45  # 单因子最高权重
    LOOKBACK_DAYS = 30

    def evaluate_factors(
        self,
        factor_performance: Dict[str, FactorPerformance],
        current_weights: Dict[str, float],
    ) -> List[FactorWeightAdjustment]:
        """评估并建议因子权重调整。"""
        adjustments: List[FactorWeightAdjustment] = []

        for factor_name, perf in factor_performance.items():
            current = current_weights.get(factor_name, 0.0)

            # 样本不足，保持不变
            if perf.sample_count < 15:
                continue

            # 失效因子：停用
            if perf.ic_30d < self.IC_DEAD:
                adjustments.append(
                    FactorWeightAdjustment(
                        factor_name=factor_name,
                        current_weight=current,
                        suggested_weight=0.0,
                        adjustment_reason=f"IC转负({perf.ic_30d:.3f})，停用",
                        confidence=0.9,
                    )
                )
                continue

            # 弱因子：降权
            if perf.ic_30d < self.IC_DEGRADED:
                new_w = max(self.MIN_WEIGHT, current * 0.5)
                adjustments.append(
                    FactorWeightAdjustment(
                        factor_name=factor_name,
                        current_weight=current,
                        suggested_weight=new_w,
                        adjustment_reason=f"IC弱({perf.ic_30d:.3f})，降权50%",
                        confidence=0.8,
                    )
                )
                continue

            # 强因子：加权
            if perf.ic_30d > self.IC_STRONG and perf.sharpe_30d > 1.0:
                new_w = min(self.MAX_WEIGHT, current * 1.2)
                adjustments.append(
                    FactorWeightAdjustment(
                        factor_name=factor_name,
                        current_weight=current,
                        suggested_weight=new_w,
                        adjustment_reason=f"IC强({perf.ic_30d:.3f}) + Sharpe{perf.sharpe_30d:.2f}，加权20%",
                        confidence=0.85,
                    )
                )
                continue

            # IC 衰减检测
            if perf.ic_decay < -0.30:  # 30%衰减
                new_w = max(self.MIN_WEIGHT, current * 0.7)
                adjustments.append(
                    FactorWeightAdjustment(
                        factor_name=factor_name,
                        current_weight=current,
                        suggested_weight=new_w,
                        adjustment_reason=f"IC衰减{perf.ic_decay:.0%}，降权30%",
                        confidence=0.7,
                    )
                )

        return adjustments

    def normalize_weights(
        self, adjustments: List[FactorWeightAdjustment]
    ) -> Dict[str, float]:
        """归一化权重（确保总和=1）。"""
        weights = {a.factor_name: a.suggested_weight for a in adjustments}
        total = sum(weights.values())
        if total == 0:
            return weights
        return {k: v / total for k, v in weights.items()}


# ============================================================
# Layer 3: 策略组合自适应（市场制度联动）
# ============================================================

@dataclass(frozen=True)
class StrategyMix:
    """策略组合配置。"""

    name: str
    description: str
    enabled_strategies: List[str]
    weights: Dict[str, float]
    suitable_regimes: List[str]
    expected_sharpe: float


# 预定义的策略组合（针对不同市场制度）
STRATEGY_MIXES = {
    "aggressive_bull": StrategyMix(
        name="进攻牛市",
        description="稳定上涨期，重仓动量+涨停板",
        enabled_strategies=[
            "momentum",
            "limit_up_ladder",
            "morning_breakout",
            "sector_rotation",
        ],
        weights={
            "momentum": 0.30,
            "limit_up_ladder": 0.30,
            "morning_breakout": 0.20,
            "sector_rotation": 0.20,
        },
        suitable_regimes=["stable_bull"],
        expected_sharpe=2.5,
    ),
    "volatile_bull": StrategyMix(
        name="波动牛市",
        description="波动牛市，平衡进攻和防守",
        enabled_strategies=[
            "momentum",
            "triple_rise",
            "intraday_trade",
            "sector_rotation",
        ],
        weights={
            "momentum": 0.25,
            "triple_rise": 0.25,
            "intraday_trade": 0.25,
            "sector_rotation": 0.25,
        },
        suitable_regimes=["volatile_bull"],
        expected_sharpe=2.0,
    ),
    "defensive_bear": StrategyMix(
        name="防守熊市",
        description="熊市防守，质量+均值回归",
        enabled_strategies=[
            "quality",
            "value",
            "mean_reversion",
        ],
        weights={
            "quality": 0.40,
            "value": 0.30,
            "mean_reversion": 0.30,
        },
        suitable_regimes=["stable_bear", "volatile_bear"],
        expected_sharpe=1.0,
    ),
    "rotation_sideways": StrategyMix(
        name="震荡轮动",
        description="震荡市，多因子轮动",
        enabled_strategies=[
            "momentum",
            "mean_reversion",
            "sector_rotation",
            "intraday_trade",
        ],
        weights={
            "momentum": 0.20,
            "mean_reversion": 0.30,
            "sector_rotation": 0.30,
            "intraday_trade": 0.20,
        },
        suitable_regimes=["stable_sideways", "volatile_sideways"],
        expected_sharpe=1.5,
    ),
    "emergency_defensive": StrategyMix(
        name="紧急防守",
        description="系统风险触发，仅持有现金等价物",
        enabled_strategies=["quality"],
        weights={"quality": 1.0},
        suitable_regimes=[],
        expected_sharpe=0.5,
    ),
}


class StrategyMixAdaptor:
    """策略组合自适应。

    根据当前市场制度，自动切换最适合的策略组合。
    """

    def select_mix(self, regime: str) -> StrategyMix:
        """根据市场制度选择策略组合。"""
        for mix in STRATEGY_MIXES.values():
            if regime in mix.suitable_regimes:
                return mix
        # 默认震荡市
        return STRATEGY_MIXES["rotation_sideways"]

    def get_mix_by_name(self, name: str) -> Optional[StrategyMix]:
        return STRATEGY_MIXES.get(name)


# ============================================================
# Layer 4: 元策略学习
# ============================================================

@dataclass(frozen=True)
class StrategyPerformanceRecord:
    """策略表现记录。"""

    strategy_name: str
    regime: str
    date_range: Tuple[date, date]
    total_signals: int
    winning_signals: int
    avg_return: float
    sharpe: float
    max_drawdown: float


@dataclass(frozen=True)
class MetaLearningRecommendation:
    """元学习建议。"""

    suggested_mix_name: str
    confidence: float
    reasoning: list[str]
    fallback_mix_name: str


class MetaStrategyLearner:
    """元策略学习器。

    根据历史表现，学习「在什么市况下，哪种策略组合最强」。

    简化版实现（不依赖 sklearn）：
    - 维护策略-制度表现矩阵
    - 用滚动 Sharpe 排序
    - 推荐当前制度下表现最好的组合
    """

    HISTORY_FILE = "data/meta_learning_history.json"

    def __init__(self):
        self.history_path = Path(self.HISTORY_FILE)
        self.records: List[StrategyPerformanceRecord] = []

    def record_performance(
        self,
        strategy_name: str,
        regime: str,
        period_start: date,
        period_end: date,
        total_signals: int,
        winning_signals: int,
        avg_return: float,
        sharpe: float,
        max_drawdown: float,
    ) -> None:
        """记录策略表现。"""
        record = StrategyPerformanceRecord(
            strategy_name=strategy_name,
            regime=regime,
            date_range=(period_start, period_end),
            total_signals=total_signals,
            winning_signals=winning_signals,
            avg_return=avg_return,
            sharpe=sharpe,
            max_drawdown=max_drawdown,
        )
        self.records.append(record)
        self._persist()

    def recommend_mix(
        self,
        current_regime: str,
        lookback_days: int = 60,
    ) -> MetaLearningRecommendation:
        """基于历史表现推荐策略组合。"""
        cutoff = today_shanghai() - timedelta(days=lookback_days)
        relevant_records = [
            r for r in self.records
            if r.regime == current_regime and r.date_range[1] >= cutoff
        ]

        if not relevant_records:
            # 没有历史数据，按制度默认
            mix_adaptor = StrategyMixAdaptor()
            mix = mix_adaptor.select_mix(current_regime)
            return MetaLearningRecommendation(
                suggested_mix_name=mix.name,
                confidence=0.5,
                reasoning=["无历史数据，使用默认匹配"],
                fallback_mix_name="rotation_sideways",
            )

        # 按策略聚合表现
        strategy_scores: Dict[str, list[float]] = {}
        for r in relevant_records:
            if r.strategy_name not in strategy_scores:
                strategy_scores[r.strategy_name] = []
            # 综合评分：Sharpe + 胜率 - 回撤
            win_rate = r.winning_signals / r.total_signals if r.total_signals > 0 else 0
            score = r.sharpe + win_rate * 2 - r.max_drawdown * 5
            strategy_scores[r.strategy_name].append(score)

        # 平均评分
        avg_scores = {
            name: float(np.mean(scores))
            for name, scores in strategy_scores.items()
        }

        # 找出表现最好的 3-4 个策略
        top_strategies = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)[:4]

        # 匹配到最相似的预定义组合
        top_names = set(s[0] for s in top_strategies)
        best_mix = None
        best_overlap = 0
        for mix_name, mix in STRATEGY_MIXES.items():
            overlap = len(top_names & set(mix.enabled_strategies))
            if overlap > best_overlap:
                best_overlap = overlap
                best_mix = (mix_name, mix)

        if best_mix:
            mix_name, mix = best_mix
            confidence = min(0.9, best_overlap / 4 + 0.3)
            reasoning = [
                f"近{lookback_days}日{current_regime}制度下，",
                f"表现最好的策略：{', '.join(s[0] for s in top_strategies[:3])}",
                f"匹配组合：{mix.name}（重叠度{best_overlap}/4）",
            ]
            return MetaLearningRecommendation(
                suggested_mix_name=mix_name,
                confidence=confidence,
                reasoning=reasoning,
                fallback_mix_name="rotation_sideways",
            )

        # 没匹配，默认
        return MetaLearningRecommendation(
            suggested_mix_name="rotation_sideways",
            confidence=0.4,
            reasoning=["无法匹配策略组合，使用默认"],
            fallback_mix_name="rotation_sideways",
        )

    def _persist(self) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    **asdict(r),
                    "date_range": [r.date_range[0].isoformat(), r.date_range[1].isoformat()],
                }
                for r in self.records
            ]
            self.history_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass


# ============================================================
# 回滚机制
# ============================================================

@dataclass(frozen=True)
class ConfigSnapshot:
    """配置快照（用于回滚）。"""

    snapshot_id: str
    timestamp: datetime
    factor_weights: Dict[str, float]
    strategy_mix: str
    description: str
    performance_baseline: Dict[str, float]  # baseline 表现指标


class RollbackManager:
    """配置回滚管理。

    机制：
    1. 每次自进化前 → 保存当前配置快照
    2. 进化后监控 N 天表现
    3. 表现下降超过阈值 → 自动回滚
    """

    SNAPSHOT_FILE = "data/config_snapshots.json"
    DEGRADATION_THRESHOLD = -0.20  # Sharpe 下降 20% 触发回滚

    def __init__(self):
        self.snapshot_path = Path(self.SNAPSHOT_FILE)
        self.snapshots: List[ConfigSnapshot] = []
        self._load()

    def save_snapshot(
        self,
        factor_weights: Dict[str, float],
        strategy_mix: str,
        description: str,
        baseline_sharpe: float,
        baseline_win_rate: float,
    ) -> str:
        """保存配置快照。"""
        timestamp = now_shanghai()
        snapshot_id = f"snap_{timestamp.strftime('%Y%m%d_%H%M%S')}"
        snapshot = ConfigSnapshot(
            snapshot_id=snapshot_id,
            timestamp=timestamp,
            factor_weights=dict(factor_weights),
            strategy_mix=strategy_mix,
            description=description,
            performance_baseline={
                "sharpe": baseline_sharpe,
                "win_rate": baseline_win_rate,
            },
        )
        self.snapshots.append(snapshot)
        # 只保留最近 10 个
        self.snapshots = self.snapshots[-10:]
        self._persist()
        return snapshot_id

    def should_rollback(
        self,
        current_sharpe: float,
        snapshot_id: str,
    ) -> Tuple[bool, str]:
        """检查是否应该回滚。"""
        snapshot = next((s for s in self.snapshots if s.snapshot_id == snapshot_id), None)
        if not snapshot:
            return False, "快照不存在"

        baseline = snapshot.performance_baseline.get("sharpe", 0)
        if baseline <= 0:
            return False, "baseline 无效"

        change = (current_sharpe - baseline) / abs(baseline)
        if change < self.DEGRADATION_THRESHOLD:
            return True, f"Sharpe 下降 {change:.0%}（{baseline:.2f} → {current_sharpe:.2f}），建议回滚"

        return False, f"表现稳定（变化 {change:+.0%}）"

    def rollback_to(self, snapshot_id: str) -> Optional[ConfigSnapshot]:
        """回滚到指定快照。"""
        return next((s for s in self.snapshots if s.snapshot_id == snapshot_id), None)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            self.snapshots = [
                ConfigSnapshot(
                    snapshot_id=d["snapshot_id"],
                    timestamp=datetime.fromisoformat(d["timestamp"]),
                    factor_weights=d["factor_weights"],
                    strategy_mix=d["strategy_mix"],
                    description=d["description"],
                    performance_baseline=d["performance_baseline"],
                )
                for d in data
            ]
        except Exception:
            self.snapshots = []

    def _persist(self) -> None:
        try:
            self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    "snapshot_id": s.snapshot_id,
                    "timestamp": s.timestamp.isoformat(),
                    "factor_weights": s.factor_weights,
                    "strategy_mix": s.strategy_mix,
                    "description": s.description,
                    "performance_baseline": s.performance_baseline,
                }
                for s in self.snapshots
            ]
            self.snapshot_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass


# ============================================================
# 统一进化调度器
# ============================================================

class AdaptiveEvolutionCoordinator:
    """4层自进化统一调度。

    使用流程：
    1. 每周：因子权重评估 → FactorWeightAdaptor
    2. 每日：根据市场制度选择策略组合 → StrategyMixAdaptor
    3. 每月：元学习推荐 → MetaStrategyLearner
    4. 每次变更：保存快照，监控表现，必要时回滚 → RollbackManager
    """

    def __init__(self):
        self.factor_adaptor = FactorWeightAdaptor()
        self.mix_adaptor = StrategyMixAdaptor()
        self.meta_learner = MetaStrategyLearner()
        self.rollback_mgr = RollbackManager()

    def daily_adapt(
        self,
        current_regime: str,
        is_system_halt: bool = False,
    ) -> StrategyMix:
        """每日策略组合调整。"""
        if is_system_halt:
            return STRATEGY_MIXES["emergency_defensive"]

        # 优先用元学习推荐
        recommendation = self.meta_learner.recommend_mix(current_regime)
        if recommendation.confidence > 0.7:
            mix = self.mix_adaptor.get_mix_by_name(recommendation.suggested_mix_name)
            if mix:
                return mix

        # 否则按制度匹配
        return self.mix_adaptor.select_mix(current_regime)

    def weekly_factor_review(
        self,
        factor_performance: Dict[str, FactorPerformance],
        current_weights: Dict[str, float],
        current_sharpe: float,
        current_win_rate: float,
    ) -> Tuple[List[FactorWeightAdjustment], str]:
        """每周因子权重评估。

        返回：(调整建议列表, 快照ID)
        """
        # 保存快照
        snapshot_id = self.rollback_mgr.save_snapshot(
            factor_weights=current_weights,
            strategy_mix="current",
            description="周度因子评估前快照",
            baseline_sharpe=current_sharpe,
            baseline_win_rate=current_win_rate,
        )

        # 评估调整
        adjustments = self.factor_adaptor.evaluate_factors(
            factor_performance, current_weights
        )

        return adjustments, snapshot_id

    def check_rollback_needed(
        self,
        snapshot_id: str,
        current_sharpe: float,
    ) -> Tuple[bool, str]:
        """检查是否需要回滚。"""
        return self.rollback_mgr.should_rollback(current_sharpe, snapshot_id)
