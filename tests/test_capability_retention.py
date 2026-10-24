"""Prove the guard rejects capability removal rather than merely equal totals."""

import json

import pytest

from scripts.check_capability_retention import (
    CapabilityBaseline, check_retention, declared_routes, load_baseline, main,
)


def _baseline() -> CapabilityBaseline:
    return CapabilityBaseline(
        "a" * 40, 2, frozenset({("GET", "/api/research"), ("POST", "/api/report")}),
        (("tests/test_backtest.py", 2), ("tests/test_strategy.py", 1)),
    )


def _nodes() -> list[str]:
    return ["tests/test_backtest.py::test_raw_prices", "tests/test_backtest.py::test_pit", "tests/test_strategy.py::test_score"]


def test_capability_guard_accepts_additions_when_historical_capabilities_kept() -> None:
    baseline = _baseline()
    result = check_retention(baseline, baseline.routes | {("GET", "/api/new")}, _nodes() + ["tests/test_strategy.py::test_extra"])
    assert not result.errors and result.routes == 3 and result.protected_tests == 4


def test_capability_guard_rejects_same_count_swap_when_original_route_removed() -> None:
    result = check_retention(_baseline(), frozenset({("GET", "/api/research"), ("POST", "/api/new")}), _nodes())
    assert result.errors == ("removed HTTP endpoint: POST /api/report",)


def test_capability_guard_rejects_deleted_tests_when_other_file_gains_nodes() -> None:
    result = check_retention(_baseline(), _baseline().routes, [*_nodes()[1:], "tests/test_strategy.py::test_extra"])
    assert result.errors == ("research tests reduced: tests/test_backtest.py: 1 < 2",)


def test_capability_guard_does_not_double_count_when_collection_has_duplicates() -> None:
    result = check_retention(_baseline(), _baseline().routes, _nodes()[1:] * 2)
    assert result.errors
    assert check_retention(_baseline(), frozenset(), []).errors


def test_capability_guard_extracts_literal_routes_when_nested_and_async() -> None:
    source = '''
@app.get("/api/research")
async def read(): pass
@app.post("/api/report")
def write(): pass
# @app.delete("/api/comment")
@other.get("/api/unrelated")
def unrelated(): pass
'''
    assert declared_routes(source) == _baseline().routes


@pytest.mark.parametrize("bad", [[], {}, {"schema_version": True}, {"schema_version": 9}, {"schema_version": 1, "source_commit": "short"}])
def test_capability_guard_fails_closed_when_baseline_malformed(tmp_path, bad) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        load_baseline(path)


def test_capability_guard_cli_fails_when_collection_missing(tmp_path) -> None:
    assert main(["--root", str(tmp_path), "--collected-tests", str(tmp_path / "missing")]) == 1
