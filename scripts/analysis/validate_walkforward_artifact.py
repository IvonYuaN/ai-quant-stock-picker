#!/usr/bin/env python3
"""校验 walk-forward gate 产物并输出可审计的 JSON 证据。

该脚本只读 gate sidecar，不修改生产数据、不调整阈值，也不生成推荐。
退出码 0 表示结构与双门校验通过；退出码 1 表示证据不可作为正式推荐依据。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from aqsp.core.time import now_shanghai
from aqsp.walkforward_gate import (
    validate_walkforward_gate_payload,
    validate_walkforward_market_coverage,
)


def audit_artifact(
    path: Path,
    *,
    today: date,
    expected_thresholds_version: str | None = None,
    expected_cost_mode: str | None = None,
) -> dict[str, Any]:
    """读取并审计 sidecar，返回可序列化的结构化证据。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": "unreadable", "reasons": [str(exc)]}
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "status": "invalid",
            "reasons": ["sidecar top-level must be an object"],
        }

    validation = validate_walkforward_gate_payload(
        payload,
        today=today,
        expected_thresholds_version=expected_thresholds_version,
    )
    coverage = validate_walkforward_market_coverage(payload)
    reasons = [*validation.blockers, *coverage.blockers]
    cost_mode = payload.get("cost_mode")
    if expected_cost_mode is not None and cost_mode != expected_cost_mode:
        reasons.append(
            f"cost_mode mismatch: {cost_mode!r} != {expected_cost_mode!r}"
        )
    return {
        "ok": not reasons,
        "status": "pass" if not reasons else "blocked",
        "path": str(path),
        "run_date": validation.run_date.isoformat()
        if validation.run_date is not None
        else None,
        "data_end": validation.data_end.isoformat()
        if validation.data_end is not None
        else None,
        "dsr": validation.dsr,
        "pbo": validation.pbo,
        "n_variants": validation.n_variants,
        "thresholds_version": validation.thresholds_version,
        "cost_mode": cost_mode,
        "effective_symbols": coverage.effective_symbols,
        "coverage_ratio": coverage.coverage_ratio,
        "reasons": reasons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--today", type=date.fromisoformat)
    parser.add_argument("--expected-thresholds-version")
    parser.add_argument("--expected-cost-mode", choices=("legacy", "net"))
    args = parser.parse_args(argv)
    result = audit_artifact(
        args.path,
        today=args.today or now_shanghai().date(),
        expected_thresholds_version=args.expected_thresholds_version,
        expected_cost_mode=args.expected_cost_mode,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
