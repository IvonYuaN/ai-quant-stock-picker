#!/usr/bin/env python3
"""Deterministically build the T3-patched gate wrapper (``t3_gate_script.py``).

Why this exists
---------------
The T3 dual-window campaign runs ``--grid-profile htf_mr`` against a full-market
sqlite database that has no index table. The tracked wrapper
``scripts/run_production_walkforward_gate.py`` exposes neither, so the runner
historically carried a **hand-built** copy at ``/opt/aqsp-runner/t3_gate_script.py``
("extract from branch + two patches"). That file was never committed, so when the
runner VM was re-imaged (2026-09-17) it was destroyed with the rest of
``/opt/aqsp-runner`` and the campaign could not be restarted.

This builder reproduces the two patches deterministically from the tracked source
and **refuses to emit output** when either anchor does not apply, so a silently
broken wrapper can never be produced.

Patches
-------
``P1``  ``--grid-profile`` choices gain ``htf_mr`` (the campaign's arm).
``P2``  ``--benchmark-symbol ""`` is injected into the child
        ``aqsp walkforward`` command, skipping index fetching: the full-market DB
        has no ``000300``, so the default would abort with
        ``sqlite_db 指数获取失败``.

Usage
-----
    python scripts/build_t3_gate_script.py --out /tmp/t3_gate_script.py

Then ship it to the runner as ``/opt/aqsp-runner/t3_gate_script.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "scripts" / "run_production_walkforward_gate.py"

# P1 — the parser's own choices tuple (the wrapper does not inherit the CLI's).
CHOICES_ANCHOR = '        choices=("stable", "stable_plus", "exploratory"),\n'
CHOICES_PATCHED = '        choices=("stable", "stable_plus", "exploratory", "htf_mr"),\n'

# P2 — anchor is the child command's pool block; insertion keeps argument order stable.
POOL_ANCHOR = '        "--pool",\n        "all",\n'
POOL_PATCHED = '        "--pool",\n        "all",\n        "--benchmark-symbol",\n        "",\n'


class BuildError(RuntimeError):
    """Raised when a patch anchor does not apply exactly once."""


def _apply_once(text: str, anchor: str, patched: str, patch_id: str) -> str:
    """Apply one patch, failing loudly instead of emitting a partial result."""
    if patched in text and anchor not in text:
        raise BuildError(f"{patch_id}: source already patched; refusing to double-apply")
    matches = text.count(anchor)
    if matches != 1:
        raise BuildError(
            f"{patch_id}: anchor matched {matches} times (expected exactly 1); "
            "the tracked source drifted — update the anchor, never hand-edit the output"
        )
    return text.replace(anchor, patched, 1)


def build(source_text: str) -> str:
    """Return the patched wrapper source.

    Raises:
        BuildError: if either anchor does not match exactly once.
    """
    patched = _apply_once(
        source_text, CHOICES_ANCHOR, CHOICES_PATCHED, "P1(grid-profile choices)"
    )
    return _apply_once(patched, POOL_ANCHOR, POOL_PATCHED, "P2(benchmark-symbol)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the T3-patched gate wrapper from the tracked source."
    )
    parser.add_argument("--out", required=True, help="write the patched wrapper here")
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help="tracked wrapper to patch (default: scripts/run_production_walkforward_gate.py)",
    )
    args = parser.parse_args(argv)

    source_path = Path(args.source)
    if not source_path.is_file():
        raise SystemExit(f"source wrapper missing: {source_path}")

    try:
        patched = build(source_path.read_text(encoding="utf-8"))
    except BuildError as exc:
        raise SystemExit(f"build refused: {exc}") from exc

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(patched, encoding="utf-8")
    print(f"wrote {out} ({len(patched.splitlines())} lines) from {source_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
