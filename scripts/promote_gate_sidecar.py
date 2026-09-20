#!/usr/bin/env python3
"""把 runner 拉回的双门 sidecar 提升为 prod 生效的那一份。

背景（2026-09-20）
------------------
三段式的最后一环此前是**手工**的：``runner_fetch.sh`` 只落 ``runner.*`` 前缀文件，
注释写着「核对无误后再决定是否覆盖 prod 自己的 gate 结果」。但 prod 侧早已没有门禁
（``0 22 * * 6`` 已注释），**没有任何任务会写** ``/opt/aqsp/data/walkforward_gate.json``，
于是 runner 每周算出的判定**永远不会生效**，通知门禁一直读着上一次人工留下的旧
sidecar（且那份是 ``stable``/5 变体，结构上永远过不了 ``MIN_CSCV_VARIANTS=8``）。

这个脚本补上那一环，但**不是「未经验证就悄悄生效」**：

* 必须通过 ``validate_walkforward_gate_payload`` 的**结构性**校验
  （run_date / 新鲜度 / 数值字段 / n_periods / held-out 污染 / 窗口一致性）；
  **判定类 blocker（DSR 未过门 / PBO 未过门 / CSCV 变体不足）不算失败** ——
  门禁本来就该 fail，提升的是「一份真实、完整、未过期的判定」，不是「一份通过的判定」。
* 采用**否定式白名单**：只有已知的判定类 blocker 才放行；出现任何**没见过的**
  blocker 一律拒绝（fail-closed）。将来新增校验项时会自动变保守，而不是自动放行。
* 旧文件先归档到 ``--archive-dir``，可回滚。
* 原子替换（临时文件 + ``os.replace``）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

EXIT_PROMOTED = 0
EXIT_REFUSED = 3
EXIT_INPUT = 4

# 「策略没过门」类 blocker：说明判定结果是不通过，但**这份 sidecar 本身是真实完整的**，
# 因此可以提升 —— 门禁本来就该 fail，我们提升的是「一份真实的判定」，不是「一份通过的判定」。
#
# 注意这里**刻意不包含**下面这些，它们属于「证据本身不可信」，必须阻止提升：
#   * ``pbo_valid flag ...``      —— PBO 没经真实 CSCV（占位值），不是判定而是缺证据
#   * ``cscv_reliable flag ...``  —— 变体数不足，PBO 系统性上偏
#   * ``n_variants=N < 8 ...``    —— 同上
#   * ``window_consistency: ...`` —— 窗口方向不一致，判定依赖窗口选择
#   * 各类 malformed / stale / held-out —— 结构性
VERDICT_BLOCKER_PREFIXES = (
    "DSR=",
    "PBO=",
    "dsr_pass flag",
    "pbo_pass flag",
    "both_pass flag",
)


def _verdict_only_blockers(blockers: tuple[str, ...]) -> list[str]:
    return [b for b in blockers if not b.startswith(VERDICT_BLOCKER_PREFIXES)]


def _heldout_cutoff() -> date:
    # 与通知门禁同一口径，避免两处各写一个日期。
    from aqsp.cli import HELDOUT_TRAIN_CUTOFF

    return date.fromisoformat(HELDOUT_TRAIN_CUTOFF)


def evaluate(fetched: Path, *, today: date, max_age_days: int) -> tuple[bool, str]:
    """返回 (是否可提升, 说明)。"""
    from aqsp.walkforward_gate import validate_walkforward_gate_payload

    try:
        payload = json.loads(fetched.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"sidecar 不可读或不是合法 JSON: {exc}"
    if not isinstance(payload, dict):
        return False, "sidecar 顶层不是对象"

    validation = validate_walkforward_gate_payload(
        payload,
        today=today,
        max_age_days=max_age_days,
        heldout_cutoff=_heldout_cutoff(),
    )
    structural = _verdict_only_blockers(tuple(validation.blockers))
    if structural:
        return False, "结构性问题: " + "; ".join(structural)
    return True, (
        f"run_date={validation.run_date} age={validation.age_days}d "
        f"DSR={validation.dsr} PBO={validation.pbo} n_variants={validation.n_variants} "
        f"both_pass={validation.both_pass}"
    )


def promote(
    *,
    fetched: Path,
    target: Path,
    archive_dir: Path | None,
    today: date,
    max_age_days: int,
    dry_run: bool = False,
) -> int:
    ok, detail = evaluate(fetched, today=today, max_age_days=max_age_days)
    if not ok:
        print(f"REFUSED: {detail}")
        return EXIT_REFUSED

    if dry_run:
        print(f"WOULD_PROMOTE: {detail}")
        return EXIT_PROMOTED

    if archive_dir is not None and target.exists():
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = today.strftime("%Y%m%d")
        shutil.copy2(target, archive_dir / f"walkforward_gate.{stamp}.json")

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    shutil.copy2(fetched, tmp)
    tmp.replace(target)
    print(f"PROMOTED: {detail}")
    return EXIT_PROMOTED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetched", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--archive-dir", type=Path, default=None)
    parser.add_argument("--max-age-days", type=int, default=35)
    parser.add_argument("--today", default=None, help="ISO 日期，仅测试用")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.fetched.exists():
        print(f"REFUSED: 拉回的文件不存在: {args.fetched}")
        return EXIT_INPUT

    today = date.fromisoformat(args.today) if args.today else date.today()
    return promote(
        fetched=args.fetched,
        target=args.target,
        archive_dir=args.archive_dir,
        today=today,
        max_age_days=args.max_age_days,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
