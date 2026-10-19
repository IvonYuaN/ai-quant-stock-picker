"""Tests for the #199 gate-result comparison helper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "analysis"
    / "compare_gate_results.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("compare_gate_results", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cgr = _load_module()


def _write_gate(path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "run_date": "2026-10-19",
        "grid_profile": "stable_plus",
        "cost_mode": "legacy",
        "n_variants": 8,
        "cscv_reliable": True,
        "pbo_valid": True,
        "data_start": "2023-10-17",
        "data_end": "2026-10-16",
        "n_periods": 20,
        "deflated_sharpe": -0.4891,
        "pbo": 0.7103,
        "dsr_pass": False,
        "pbo_pass": False,
        "both_pass": False,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _row(text: str, label: str) -> str:
    """Return the rendered data row whose first column is ``label``."""
    for line in text.splitlines():
        if line.startswith(label + " ") or line == label:
            return line
    raise AssertionError(f"row {label!r} not found in:\n{text}")


def test_render_includes_headers_and_marks_missing(tmp_path: Path) -> None:
    a = _write_gate(tmp_path / "baseline.gate.json")
    b = _write_gate(tmp_path / "net.gate.json", cost_mode="net", pbo=0.55)
    missing = tmp_path / "shrink.gate.json"  # never written (load-guard skip)

    text = cgr.render(cgr.collect([a, b, missing]))

    for header in ("experiment", "profile", "cost", "N", "cscv", "DSR", "PBO", "BOTH"):
        assert header in text
    assert "baseline" in text and "net" in text and "shrink" in text
    assert "-0.4891" in text  # DSR rendered with sign
    assert "legacy" in text and "net" in text
    assert "<missing>" in text  # shrink row rendered, not crashed
    assert "FAIL" in text  # both_pass False -> FAIL


def test_render_preserves_input_order(tmp_path: Path) -> None:
    a = _write_gate(tmp_path / "baseline.gate.json")
    b = _write_gate(tmp_path / "net.gate.json", cost_mode="net")
    lines = cgr.render(cgr.collect([a, b])).splitlines()
    assert lines[1].startswith("baseline")
    assert lines[2].startswith("net")


def test_pass_pass_fail_is_explained_by_cscv_column(tmp_path: Path) -> None:
    """The #199 trap: both gates pass yet the verdict is FAIL.

    A shrunken grid can pass DSR and PBO individually while the CSCV split is
    too thin to trust, in which case ``both_pass`` is forced False.  The table
    must show *why* instead of looking like a rendering bug.
    """
    p = _write_gate(
        tmp_path / "shrink.gate.json",
        n_variants=6,
        cscv_reliable=False,
        dsr_pass=True,
        pbo_pass=True,
        both_pass=False,
    )
    text = cgr.render(cgr.collect([p]))
    row = _row(text, "shrink")
    cells = row.split()
    # N=6, cscv=FAIL, pbo_vld=PASS, dsr_ok=PASS, pbo_ok=PASS, BOTH=FAIL
    assert "6" in cells
    assert cells.count("PASS") == 3
    assert cells.count("FAIL") == 2
    assert cells[-1] == "FAIL"


def test_metric_renders_as_float_even_when_json_has_int(tmp_path: Path) -> None:
    """json turns ``0.0`` into ``0``; a metric must still read ``+0.0000``."""
    p = _write_gate(tmp_path / "zero.gate.json", deflated_sharpe=0, pbo=1)
    text = cgr.render(cgr.collect([p]))
    row = _row(text, "zero")
    assert "+0.0000" in row
    assert "+1.0000" in row


def test_absent_field_renders_as_dash(tmp_path: Path) -> None:
    p = _write_gate(tmp_path / "old.gate.json")
    payload = json.loads(p.read_text(encoding="utf-8"))
    for key in ("grid_profile", "cscv_reliable", "pbo_valid"):
        payload.pop(key)
    p.write_text(json.dumps(payload), encoding="utf-8")

    row = _row(cgr.render(cgr.collect([p])), "old")
    # profile / cscv / pbo_vld all absent -> "-" rather than "None"
    assert "None" not in row
    assert row.split().count("-") >= 3


def test_main_returns_2_without_inputs() -> None:
    assert cgr.main([]) == 2


def test_main_dir_globs_gate_json(tmp_path: Path, capsys) -> None:
    _write_gate(tmp_path / "baseline.gate.json")
    _write_gate(tmp_path / "net.gate.json", cost_mode="net")
    (tmp_path / "unrelated.json").write_text("{}", encoding="utf-8")  # must be ignored
    rc = cgr.main(["--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "baseline" in out and "net" in out
    assert "unrelated" not in out


def test_fmt_formats_by_kind() -> None:
    assert cgr._fmt(True, cgr.FLAG) == "PASS"
    assert cgr._fmt(False, cgr.FLAG) == "FAIL"
    assert cgr._fmt(None, cgr.FLAG) == "-"
    assert cgr._fmt(-0.4891, cgr.NUM) == "-0.4891"
    assert cgr._fmt(0, cgr.NUM) == "+0.0000"
    assert cgr._fmt(8, cgr.INT) == "8"
    assert cgr._fmt("net", cgr.TEXT) == "net"
    assert cgr._fmt(None, cgr.TEXT) == "-"
