#!/usr/bin/env python3
"""Compare #199 control-experiment gate results side by side.

Reads one or more ``walkforward_gate.json`` snapshots (as produced by
``scripts/runner/runner_gate.sh``) and prints a compact comparison of the
metrics that matter for issue #199: DSR, PBO, pass flags, grid profile,
cost mode and the evaluated window.

The point of #199 is to decide whether the DSR/PBO double-fail is driven by
weak signal, by selection bias (too large a grid) or by an optimistic cost
model.  That decision needs the three control runs laid out next to each
other, which is what this helper renders.

Two flags are easy to misread and are therefore rendered explicitly:

* ``cscv`` -- when the grid has fewer variants than ``MIN_CSCV_VARIANTS``
  the CSCV split is unreliable and ``both_pass`` is forced to ``FAIL`` even
  if ``dsr_pass`` and ``pbo_pass`` are both ``PASS``.  A row can therefore
  legitimately read ``PASS PASS FAIL``.
* ``pbo_vld`` -- whether the PBO point estimate itself was computable.

Usage:
    python scripts/analysis/compare_gate_results.py a.gate.json b.gate.json
    python scripts/analysis/compare_gate_results.py --dir /path/to/199_control
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Column kinds drive formatting.  Keeping the kind explicit (instead of
# sniffing the runtime type) matters because json gives us ``0`` for ``0.0``
# and we still want ``+0.0000`` for a metric.
TEXT = "text"
INT = "int"
NUM = "num"
FLAG = "flag"

# (gate-json key, short column header, kind).  Order matters: profile/cost
# first so the reader immediately sees which experiment a row is, and the
# verdict flags last so they line up under the eye.
FIELDS: tuple[tuple[str, str, str], ...] = (
    ("grid_profile", "profile", TEXT),
    ("cost_mode", "cost", TEXT),
    ("n_variants", "N", INT),
    ("cscv_reliable", "cscv", FLAG),
    ("data_start", "start", TEXT),
    ("data_end", "end", TEXT),
    ("n_periods", "periods", INT),
    ("deflated_sharpe", "DSR", NUM),
    ("pbo", "PBO", NUM),
    ("pbo_valid", "pbo_vld", FLAG),
    ("dsr_pass", "dsr_ok", FLAG),
    ("pbo_pass", "pbo_ok", FLAG),
    ("both_pass", "BOTH", FLAG),
)

FOOTER = (
    "# BOTH is the authoritative verdict. A row may read PASS PASS FAIL when "
    "cscv is FAIL\n"
    "# (PBO point estimate is unreliable below MIN_CSCV_VARIANTS variants)."
)


def _load(path: Path) -> dict | None:
    """Return the parsed gate json, or ``None`` if it is missing/corrupt.

    A missing file is expected when an experiment was skipped by the runner
    load guard; the caller renders it as ``<missing>`` rather than crashing.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _fmt(value: object, kind: str = TEXT) -> str:
    """Render one cell.  ``None`` always becomes ``-`` (field absent)."""
    if value is None:
        return "-"
    if kind == FLAG:
        return "PASS" if value is True else "FAIL" if value is False else str(value)
    if kind == NUM:
        try:
            return f"{float(value):+.4f}"  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return str(value)
    if kind == INT:
        try:
            return str(int(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _label(path: Path) -> str:
    name = path.name
    if name.endswith(".gate.json"):
        return name[: -len(".gate.json")]
    if name.endswith(".json"):
        return name[: -len(".json")]
    return name


def collect(paths: list[Path]) -> list[tuple[str, dict | None]]:
    """Pair each path with its parsed payload (``None`` if unreadable)."""
    return [(_label(p), _load(p)) for p in paths]


def render(rows: list[tuple[str, dict | None]]) -> str:
    headers = ["experiment", *(h for _, h, _ in FIELDS)]
    table: list[list[str]] = [headers]
    for label, data in rows:
        if data is None:
            # Keep the label so the reader can still tell which experiment is
            # absent; only the metric columns collapse to "<missing>"/"-".
            cells = [label, "<missing>", *(("-" for _ in FIELDS[1:]))]
        else:
            cells = [label, *(_fmt(data.get(key), kind) for key, _, kind in FIELDS)]
        table.append(cells)

    widths = [max(len(row[i]) for row in table) for i in range(len(headers))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in table
    ]
    lines.append(FOOTER)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare #199 gate results.")
    parser.add_argument("paths", nargs="*", type=Path, help="gate json snapshots")
    parser.add_argument(
        "--dir", type=Path, default=None, help="directory of *.gate.json"
    )
    args = parser.parse_args(argv)

    paths = list(args.paths)
    if args.dir is not None:
        paths.extend(sorted(args.dir.glob("*.gate.json")))
    if not paths:
        print("no gate json given", file=sys.stderr)
        return 2

    print(render(collect(paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
