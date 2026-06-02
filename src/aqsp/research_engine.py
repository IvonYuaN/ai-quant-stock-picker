from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from aqsp.backtest.walk_forward import WalkForwardResult

ENGINE_CHOICES = ("auto", "builtin", "akquant")


@dataclass(frozen=True)
class WalkForwardEngineConfig:
    train_days: int
    test_days: int
    purge_days: int
    horizon_days: int
    fee_bps: float = 8.0
    slippage_bps: float = 5.0
    top_n: int = 10
    use_tiered_stop: bool = False
    n_variants: int = 1


@dataclass(frozen=True)
class EngineResolution:
    requested: str
    resolved: str
    mode: str
    message: str


class WalkForwardEngine(Protocol):
    engine_id: str

    def run(
        self,
        strategy: object,
        data: dict[str, pd.DataFrame],
        *,
        start_date: str | None,
        end_date: str | None,
        config: WalkForwardEngineConfig,
    ) -> WalkForwardResult: ...


class BuiltinWalkForwardEngine:
    engine_id = "builtin"

    def run(
        self,
        strategy: object,
        data: dict[str, pd.DataFrame],
        *,
        start_date: str | None,
        end_date: str | None,
        config: WalkForwardEngineConfig,
    ) -> WalkForwardResult:
        from aqsp.backtest.walk_forward import WalkForwardTester

        tester = WalkForwardTester(
            strategy=strategy,
            train_period_days=config.train_days,
            test_period_days=config.test_days,
            purge_days=config.purge_days,
            horizon_days=config.horizon_days,
            fee_bps=config.fee_bps,
            slippage_bps=config.slippage_bps,
            top_n=config.top_n,
            use_tiered_stop=config.use_tiered_stop,
            n_variants=config.n_variants,
        )
        return tester.run(data, start_date=start_date, end_date=end_date)


class AkquantWalkForwardEngine:
    engine_id = "akquant"

    def __init__(self, compat_engine: WalkForwardEngine | None = None) -> None:
        self._compat_engine = compat_engine or BuiltinWalkForwardEngine()

    def run(
        self,
        strategy: object,
        data: dict[str, pd.DataFrame],
        *,
        start_date: str | None,
        end_date: str | None,
        config: WalkForwardEngineConfig,
    ) -> WalkForwardResult:
        # 当前阶段先固定 CLI / 配置合同，保持结果稳定，
        # 后续可在此处替换为 AKQuant 原生 walk-forward 桥接。
        return self._compat_engine.run(
            strategy,
            data,
            start_date=start_date,
            end_date=end_date,
            config=config,
        )


def resolve_walkforward_engine(requested: str) -> tuple[WalkForwardEngine, EngineResolution]:
    normalized = (requested or "auto").strip().lower() or "auto"
    if normalized not in ENGINE_CHOICES:
        raise ValueError(
            f"unknown research engine: {requested}; expected one of {ENGINE_CHOICES}"
        )

    if normalized == "builtin":
        return BuiltinWalkForwardEngine(), EngineResolution(
            requested="builtin",
            resolved="builtin",
            mode="native",
            message="使用内置 Python walk-forward 引擎。",
        )

    if normalized == "akquant":
        if _akquant_importable():
            return AkquantWalkForwardEngine(), EngineResolution(
                requested="akquant",
                resolved="akquant",
                mode="compat",
                message="AKQuant 已安装；当前仓库先走 compat 模式，执行逻辑仍由内置引擎承载。",
            )
        if _allow_akquant_compat():
            return AkquantWalkForwardEngine(), EngineResolution(
                requested="akquant",
                resolved="builtin",
                mode="compat",
                message="AKQuant 未安装；先走 compat 模式，执行逻辑回退到内置引擎。",
            )
        raise RuntimeError(
            "AQSP_RESEARCH_ENGINE=akquant 但未安装 akquant，且 AQSP_AKQUANT_ALLOW_COMPAT=false。"
        )

    if _prefer_akquant_auto() and (_akquant_importable() or _allow_akquant_compat()):
        engine, resolution = resolve_walkforward_engine("akquant")
        return engine, EngineResolution(
            requested="auto",
            resolved=resolution.resolved,
            mode=resolution.mode,
            message=f"auto 选择 AKQuant 路线：{resolution.message}",
        )
    return BuiltinWalkForwardEngine(), EngineResolution(
        requested="auto",
        resolved="builtin",
        mode="native",
        message="auto 默认选择内置 Python walk-forward 引擎。",
    )


def _allow_akquant_compat() -> bool:
    return os.getenv("AQSP_AKQUANT_ALLOW_COMPAT", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _prefer_akquant_auto() -> bool:
    return os.getenv("AQSP_PREFER_AKQUANT", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _akquant_importable() -> bool:
    return importlib.util.find_spec("akquant") is not None
