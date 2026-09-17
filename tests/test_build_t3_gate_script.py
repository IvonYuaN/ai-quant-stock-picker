"""Tests for :mod:`scripts.build_t3_gate_script`.

The T3 dual-window campaign needs a gate wrapper that accepts ``--grid-profile
htf_mr`` and passes ``--benchmark-symbol ""`` to the child ``aqsp walkforward``
command (the full-market sqlite DB has no index table, so the default
``000300`` would abort). That wrapper used to live only on the runner as a
**hand-built** file and was destroyed when the runner VM was re-imaged, taking
the campaign with it.

These tests pin the builder so the wrapper is never again produced by hand:

* it applies **exactly** the two documented patches (byte-level diff check), and
* it **refuses to emit output** when an anchor no longer matches (source drift),
  instead of silently shipping an unpatched wrapper.

The fail-closed property is the point: a silently broken wrapper would only
surface hours into a run.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import importlib.util
import inspect
import re
import sys
from pathlib import Path

import pytest

from scripts.build_t3_gate_script import (
    CHOICES_ANCHOR,
    CHOICES_PATCHED,
    POOL_ANCHOR,
    POOL_PATCHED,
    BuildError,
    build,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKED_WRAPPER = REPO_ROOT / "scripts" / "run_production_walkforward_gate.py"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _tracked_source() -> str:
    return TRACKED_WRAPPER.read_text(encoding="utf-8")


def _changed_hunks(before: str, after: str) -> list[str]:
    """Return unified-diff lines that actually change content (no headers)."""
    diff = difflib.unified_diff(
        before.splitlines(), after.splitlines(), lineterm="", n=0
    )
    return [line for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---")]


def _load_built_module(source_text: str, tmp_path: Path):
    """Import the built wrapper.

    ``sys.modules`` must be populated *before* ``exec_module``: the wrapper uses
    ``@dataclass(frozen=True)``, and ``dataclasses`` resolves the defining module
    through ``sys.modules`` (an unregistered module raises ``AttributeError``).
    """
    path = tmp_path / "t3_gate_script.py"
    path.write_text(source_text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("t3_gate_script_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


def test_build_applies_exactly_the_two_documented_patches() -> None:
    """The output differs from the tracked source by exactly P1 and P2."""
    source = _tracked_source()
    patched = build(source)

    hunks = _changed_hunks(source, patched)
    assert sorted(hunks) == sorted(
        ["-" + CHOICES_ANCHOR.rstrip("\n"), "+" + CHOICES_PATCHED.rstrip("\n")]
        + ["+" + line for line in POOL_PATCHED.splitlines()[2:]]
    ), f"unexpected patch set: {hunks}"
    # net effect is exactly the two extra child-command arguments
    assert len(patched.splitlines()) == len(source.splitlines()) + 2


def test_build_output_is_valid_python() -> None:
    ast.parse(build(_tracked_source()))


def test_built_wrapper_accepts_htf_mr_grid_profile(tmp_path: Path) -> None:
    """P1: the *wrapper's own* parser must offer ``htf_mr``.

    The wrapper declares its own ``choices`` tuple and does not inherit the
    CLI's, so copying ``run_production_walkforward_gate.py`` verbatim yields
    ``invalid choice: 'htf_mr'`` and the campaign cannot start.
    """
    module = _load_built_module(build(_tracked_source()), tmp_path)
    parser_source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
    assert '"htf_mr"' in parser_source
    assert CHOICES_PATCHED.rstrip("\n") in parser_source


def _namespace_for(function: object) -> argparse.Namespace:
    """Build a Namespace with every ``args.<name>`` the function reads.

    Derived from the source so the test does not have to be edited each time the
    tracked wrapper gains another flag; an unset attribute would otherwise raise
    ``AttributeError`` and mask the assertion under test.
    """
    names = set(re.findall(r"\bargs\.([A-Za-z_][A-Za-z_0-9]*)", inspect.getsource(function)))
    return argparse.Namespace(**{name: "value" for name in names})


def test_built_wrapper_passes_empty_benchmark_symbol_to_child(tmp_path: Path) -> None:
    """P2: the child ``aqsp walkforward`` argv must carry ``--benchmark-symbol ""``."""
    module = _load_built_module(build(_tracked_source()), tmp_path)
    command = module.build_walkforward_command(_namespace_for(module.build_walkforward_command))

    assert "--benchmark-symbol" in command
    assert command[command.index("--benchmark-symbol") + 1] == ""
    # it must sit immediately after the pool block, i.e. inside the child argv
    assert command[command.index("--benchmark-symbol") - 2 : command.index("--benchmark-symbol")] == [
        "--pool",
        "all",
    ]


# --------------------------------------------------------------------------- #
# fail-closed: source drift must abort the build, never emit a partial wrapper
# --------------------------------------------------------------------------- #


def test_build_refuses_when_choices_anchor_is_missing() -> None:
    drifted = _tracked_source().replace(CHOICES_ANCHOR, "        choices=()\n", 1)
    with pytest.raises(BuildError, match="P1"):
        build(drifted)


def test_build_refuses_when_pool_anchor_is_missing() -> None:
    drifted = _tracked_source().replace(POOL_ANCHOR, '        "--pool",\n        "main",\n', 1)
    with pytest.raises(BuildError, match="P2"):
        build(drifted)


def test_build_refuses_double_apply() -> None:
    """Re-patching an already patched source must not silently succeed."""
    once = build(_tracked_source())
    with pytest.raises(BuildError, match="already patched"):
        build(once)


def test_build_refuses_when_anchor_matches_more_than_once() -> None:
    """Ambiguous anchors are a drift signal, not something to guess at."""
    drifted = _tracked_source().replace(CHOICES_ANCHOR, CHOICES_ANCHOR + CHOICES_ANCHOR, 1)
    with pytest.raises(BuildError, match="matched 2 times"):
        build(drifted)
