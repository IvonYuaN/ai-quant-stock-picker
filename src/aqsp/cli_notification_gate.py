"""Notification gate helpers extracted from ``cli.py``.

This module implements the walk-forward double-gate notification logic:
checking gate conditions, formatting reasons/actions, and managing
gate notification state (sent / failed / suppressed).

Constants (``HELDOUT_TRAIN_CUTOFF``, ``WALKFORWARD_GATE_PATH``,
``GATE_NOTIFY_STATE_PATH``) and small utilities (``_resolve_runtime_state_path``,
``_cold_start_min_days``) are defined here and re-exported by ``cli.py``
for backward compatibility.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from aqsp.core.time import today_shanghai
from aqsp.ledger import cold_start_min_days
from aqsp.runtime.gate_notify import (
    mark_gate_notification_failed,
    mark_gate_notification_sent,
    mark_gate_notification_suppressed,
    should_send_gate_notification,
)
from aqsp.walkforward_gate import (
    MAX_GATE_AGE_DAYS,
    WalkForwardGateValidation,
    validate_walkforward_gate_payload,
    validate_walkforward_market_coverage,
)

# 宪法 §1.3 #9：held-out 区间（2025-01~2026-04）绝对禁止用于训练
HELDOUT_TRAIN_CUTOFF = "2024-12-31"
# 宪法 §1.3 #12/#14：双门 gate 的 sidecar 文件
WALKFORWARD_GATE_PATH = "data/walkforward_gate.json"
GATE_NOTIFY_STATE_PATH = "data/gate_notify_state.json"


def _cold_start_min_days() -> int:
    return cold_start_min_days()


def _resolve_runtime_state_path(path: str) -> str:
    state_path = Path(path)
    if state_path.is_absolute():
        return str(state_path)
    project_root = Path(__file__).resolve().parents[2]
    return str(project_root / state_path)


def _check_notification_gate(
    *,
    cold_start_days: int,
    gate_path: str = WALKFORWARD_GATE_PATH,
    validation_date: date | None = None,
) -> tuple[bool, list[str]]:
    """宪法 §1.3 #12/#14：返回 (是否放行, 未达原因列表)。

    三个串联条件，缺一不可（#14 明确串联）：
      1. 冷启动 >= {COLD_START_MIN_DAYS} 个独立信号日
      2. DSR >1.0
      3. PBO <0.5
    sidecar 缺失/解析失败/过期 → fail-closed（不放行）。
    """
    reasons: list[str] = []
    cold_start_min_days = _cold_start_min_days()

    if cold_start_days < cold_start_min_days:
        reasons.append(
            f"冷启动未满: {cold_start_days}/{cold_start_min_days} 个独立信号日"
        )

    p = Path(gate_path)
    if not p.exists():
        reasons.append(f"双门 sidecar 不存在（{gate_path}）—— 请先跑 walkforward")
        return False, reasons

    try:
        gate = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"双门 sidecar 解析失败: {exc}")
        return False, reasons
    if not isinstance(gate, dict):
        reasons.append("双门 sidecar 解析失败: JSON 顶层不是对象")
        return False, reasons

    validation = validate_walkforward_gate_payload(
        gate,
        today=validation_date or today_shanghai(),
        max_age_days=MAX_GATE_AGE_DAYS,
        heldout_cutoff=date.fromisoformat(HELDOUT_TRAIN_CUTOFF),
    )
    reasons.extend(_notification_gate_reasons(gate, validation))
    reasons.extend(_notification_gate_market_coverage_reasons(gate))

    return len(reasons) == 0, reasons


def _notification_gate_market_coverage_reasons(gate: dict[str, Any]) -> list[str]:
    validation = validate_walkforward_market_coverage(gate)
    if validation.effective_symbols is None:
        return ["双门全市场覆盖缺失: effective_symbols missing"]
    if not validation.ok:
        return [
            "双门全市场覆盖不足: "
            f"{validation.effective_symbols}/{validation.min_symbols} 个有效标的"
        ]
    return []


def _notification_gate_reasons(
    gate: dict[str, Any], validation: WalkForwardGateValidation
) -> list[str]:
    raw_reasons: list[str] = []
    internal_flags: list[str] = []
    for blocker in validation.blockers:
        if blocker.startswith("run_date"):
            raw_reasons.append(f"双门 sidecar run_date 异常: {gate.get('run_date')!r}")
        elif blocker.startswith("gate stale"):
            age = validation.age_days if validation.age_days is not None else "?"
            raw_reasons.append(
                f"双门结果过期: {age} 天前（上限 {MAX_GATE_AGE_DAYS} 天）—— 请重新跑 walkforward"
            )
        elif blocker.startswith("deflated_sharpe"):
            raw_reasons.append("DSR 字段缺失或格式异常")
        elif blocker.startswith("DSR="):
            raw_reasons.append(f"DSR 未过门: {validation.dsr:.4f}（需 >1.0）")
        elif blocker.startswith("pbo missing"):
            raw_reasons.append("PBO 字段缺失或格式异常")
        elif blocker.startswith("PBO="):
            if validation.pbo == 0.0 and validation.pbo_valid is not True:
                raw_reasons.append(
                    "PBO 未通过: 当前为单策略占位 0.00%，缺少多变体 CSCV 证据"
                )
            else:
                raw_reasons.append(
                    f"PBO 未过门: {validation.pbo:.2%}（需 0 < PBO < 50%）"
                )
        elif blocker.startswith("dsr_pass"):
            internal_flags.append("dsr_pass")
        elif blocker.startswith("pbo_pass"):
            internal_flags.append("pbo_pass")
        elif blocker.startswith("pbo_valid"):
            internal_flags.append("pbo_valid")
        elif blocker.startswith("both_pass"):
            internal_flags.append("both_pass")
        elif blocker.startswith("n_periods"):
            raw_reasons.append(
                f"双门 sidecar 无有效回测周期（n_periods={gate.get('n_periods')}）"
                "—— 需真正跑 walkforward 后重写"
            )
        elif blocker.startswith("data_end malformed"):
            raw_reasons.append(
                f"双门 sidecar 的 data_end 格式异常（{gate.get('data_end')!r}）—— fail-closed"
            )
        elif blocker.startswith("data_end="):
            raw_reasons.append(
                f"双门成绩用了 held-out 数据（data_end={gate.get('data_end')} > "
                f"{HELDOUT_TRAIN_CUTOFF}）—— 不得用于解锁推送（§1.3 #9）"
            )
        else:
            raw_reasons.append(f"双门 sidecar 未通过: {blocker}")

    has_metric_reason = any(
        item.startswith(("DSR 未过门", "PBO 未过门", "PBO 未通过"))
        for item in raw_reasons
    )
    if internal_flags and not has_metric_reason:
        raw_reasons.append("双门 sidecar 内部通过标志未全部为真")

    return _dedupe_gate_reasons(raw_reasons)


def _dedupe_gate_reasons(reasons: list[str]) -> list[str]:
    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped


def _notification_gate_actions(
    reasons: list[str],
    *,
    cold_start_days: int,
) -> list[str]:
    actions: list[str] = []
    joined = " ".join(reasons)

    if "冷启动未满" in joined:
        cold_start_min_days = _cold_start_min_days()
        remaining_days = max(cold_start_min_days - cold_start_days, 0)
        actions.append(
            "继续按日运行主链，先把冷启动样本积累到 "
            f"{cold_start_min_days} 个独立信号日"
            + (f"（当前还差 {remaining_days} 天）" if remaining_days > 0 else "")
            + "。"
        )
    if (
        "sidecar 不存在" in joined
        or "n_periods=0" in joined
        or "过期" in joined
        or "解析失败" in joined
    ):
        actions.append(
            "重跑双门回测以刷新 gate：`.venv/bin/python3 -m aqsp walkforward --source sqlite_db --window-mode rolling_recent --grid-cscv`。"
        )
    if "单策略占位" in joined or "多变体 CSCV" in joined:
        actions.append(
            "生成多变体 grid CSCV 证据后再刷新 gate；旧归档 Markdown 不作为生产放行依据。"
        )
    if "DSR 未过门" in joined or "PBO 未过门" in joined or "PBO 未通过" in joined:
        if cold_start_days >= _cold_start_min_days():
            actions.append(
                "冷启动样本门已达标；当前不是冷启动卡住，是 walk-forward 双门质量门未过。"
            )
        actions.append("在双门过线前保留观察模式，不要开启自动通知或放大纸面仓位。")
    if "held-out" in joined:
        actions.append(
            "legacy_train 研究窗口需要退回 held-out 边界内，生产模式统一走 rolling_recent。"
        )

    if not actions:
        actions.append("先处理上述门禁原因，再重新执行 `aqsp run --notify`。")

    return actions


def _format_notification_gate_block(
    gate_reasons: list[str],
    next_actions: list[str],
) -> str:
    lines = [
        "> ⚠️ **未通过 walk-forward 双门验证，仅供观察，请勿实盘使用**",
        ">",
        "> 未达原因：",
    ]
    lines.extend(f"> - {reason}" for reason in gate_reasons)
    lines.append(">")
    lines.append("> 处理项：")
    lines.extend(f"> - {action}" for action in next_actions)
    lines.append("")
    return "\n".join(lines) + "\n"


def _gate_notification_allowed(task_id: str | None = None) -> bool:
    value = task_id if task_id is not None else os.getenv("AQSP_RUN_TASK_ID", "")
    return str(value or "").strip().lower() in {"daily", "scheduled"}


def _should_send_gate_notification(
    *,
    gate_ok: bool,
    gate_reasons: list[str],
    run_date: str,
) -> bool:
    return should_send_gate_notification(
        gate_ok=gate_ok,
        gate_reasons=gate_reasons,
        state_path=_resolve_runtime_state_path(
            os.getenv("AQSP_GATE_NOTIFY_STATE_PATH", GATE_NOTIFY_STATE_PATH)
        ),
        run_date=run_date,
    )


def _mark_gate_notification_sent(
    *,
    gate_reasons: list[str],
    run_date: str,
) -> None:
    mark_gate_notification_sent(
        gate_reasons=gate_reasons,
        state_path=_resolve_runtime_state_path(
            os.getenv("AQSP_GATE_NOTIFY_STATE_PATH", GATE_NOTIFY_STATE_PATH)
        ),
        run_date=run_date,
    )


def _mark_gate_notification_failed(
    *,
    gate_reasons: list[str],
    run_date: str,
) -> None:
    mark_gate_notification_failed(
        gate_reasons=gate_reasons,
        state_path=_resolve_runtime_state_path(
            os.getenv("AQSP_GATE_NOTIFY_STATE_PATH", GATE_NOTIFY_STATE_PATH)
        ),
        run_date=run_date,
    )


def _mark_gate_notification_suppressed(
    *,
    gate_reasons: list[str],
    run_date: str,
) -> None:
    mark_gate_notification_suppressed(
        gate_reasons=gate_reasons,
        state_path=_resolve_runtime_state_path(
            os.getenv("AQSP_GATE_NOTIFY_STATE_PATH", GATE_NOTIFY_STATE_PATH)
        ),
        run_date=run_date,
    )
