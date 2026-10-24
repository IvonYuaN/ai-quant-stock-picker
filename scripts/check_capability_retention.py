"""Guard the original T+1 PRD's retained HTTP and Python research capabilities.

Use CI's complete unsharded `pytest --collect-only -q -m 'not live'` output.
The committed baseline comes from the PRD's historical Git commit, not HEAD.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class CapabilityBaseline:
    source_commit: str
    minimum_http_method_paths: int
    routes: frozenset[tuple[str, str]]
    minimum_tests: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class RetentionResult:
    errors: tuple[str, ...]
    routes: int
    protected_tests: int


def load_baseline(path: Path) -> CapabilityBaseline:
    """Load a reviewed historical baseline; malformed contracts fail closed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported capability baseline version")
    commit = data.get("source_commit")
    rows = data.get("http_method_paths")
    tests = data.get("minimum_tests_per_file")
    minimum = data.get("minimum_http_method_paths")
    if not isinstance(commit, str) or len(commit) != 40:
        raise ValueError("baseline needs a full historical commit")
    if not isinstance(rows, list) or not rows or not all(
        isinstance(row, list) and len(row) == 2
        and all(isinstance(item, str) and item for item in row) for row in rows
    ):
        raise ValueError("baseline routes must be HTTP method/path pairs")
    routes = frozenset((method, path) for method, path in rows)
    if type(minimum) is not int or minimum != len(routes):
        raise ValueError("route minimum must match the historical identity set")
    if not isinstance(tests, dict) or not tests or not all(
        isinstance(path, str) and path.startswith("tests/")
        and type(count) is int and count > 0 for path, count in tests.items()
    ):
        raise ValueError("baseline needs positive per-file test minima")
    return CapabilityBaseline(commit, minimum, routes, tuple(sorted(tests.items())))


def declared_routes(source: str) -> frozenset[tuple[str, str]]:
    """Match the baseline's literal app decorators without importing the API."""
    routes = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "app"
                and decorator.func.attr in {"get", "post", "put", "patch", "delete", "head", "options"}
                and decorator.args and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                routes.add((decorator.func.attr.upper(), decorator.args[0].value))
    return frozenset(routes)


def check_retention(
    baseline: CapabilityBaseline,
    routes: frozenset[tuple[str, str]],
    collected_lines: Sequence[str],
) -> RetentionResult:
    """Reject removed identities or reduced tests, including same-count swaps."""
    nodes = {
        line.strip() for line in collected_lines
        if line.startswith("tests/") and "::" in line
    }
    counts = Counter(node.split("::", 1)[0] for node in nodes)
    errors = [f"removed HTTP endpoint: {method} {path}" for method, path in sorted(baseline.routes - routes)]
    if len(routes) < baseline.minimum_http_method_paths:
        errors.append(f"HTTP method/path count {len(routes)} < {baseline.minimum_http_method_paths}")
    for path, minimum in baseline.minimum_tests:
        if counts[path] < minimum:
            errors.append(f"research tests reduced: {path}: {counts[path]} < {minimum}")
    protected = sum(counts[path] for path, _ in baseline.minimum_tests)
    return RetentionResult(tuple(errors), len(routes), protected)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collected-tests", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        baseline = load_baseline(args.root / "tests/fixtures/capability_retention_baseline.json")
        result = check_retention(
            baseline, declared_routes((args.root / "backend/app.py").read_text(encoding="utf-8")),
            args.collected_tests.read_text(encoding="utf-8").splitlines(),
        )
    except (OSError, ValueError, TypeError, SyntaxError) as error:
        print(f"capability retention failed: {error}")
        return 1
    print(json.dumps({
        "ok": not result.errors, "baseline_commit": baseline.source_commit,
        "http_method_paths": result.routes, "protected_test_nodes": result.protected_tests,
        "errors": result.errors,
    }, ensure_ascii=False))
    return int(bool(result.errors))


if __name__ == "__main__":
    raise SystemExit(main())
