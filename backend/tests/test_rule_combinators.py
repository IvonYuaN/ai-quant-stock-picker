"""Monitor 规则组合引擎测试：三值真值表、fail-closed、冷却、载荷契约、API。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import rule_combinators as rc
import rule_combinators_api as api


def _leaf(leaf_id, metric, *, threshold=0.0, severity="info", operator=">"):
    return {
        "id": leaf_id,
        "metric": metric,
        "operator": operator,
        "threshold": threshold,
        "severity": severity,
    }


def _operand(symbol, index):
    """按 T/F/U 构造一个恒为该真值的叶子及其观测值。"""
    metric = f"x{index}"
    node = _leaf(metric, metric)
    if symbol == "T":
        return node, {metric: 1.0}
    if symbol == "F":
        return node, {metric: -1.0}
    return node, {}  # U：不提供观测值，纯函数判为 UNKNOWN


def _combine(op, *symbols):
    children, observations = [], {}
    for index, symbol in enumerate(symbols):
        node, obs = _operand(symbol, index)
        children.append(node)
        observations.update(obs)
    return {"op": op, "children": children}, observations


def _truth(op, *symbols):
    tree, observations = _combine(op, *symbols)
    return rc.evaluate_combination(tree, observations).truth


# ---- AND / OR / NOT 三值真值表 ----

AND_CASES = [
    ("T", "T", "TRUE"), ("T", "F", "FALSE"), ("T", "U", "UNKNOWN"),
    ("F", "T", "FALSE"), ("F", "F", "FALSE"), ("F", "U", "FALSE"),
    ("U", "T", "UNKNOWN"), ("U", "F", "FALSE"), ("U", "U", "UNKNOWN"),
]
OR_CASES = [
    ("T", "T", "TRUE"), ("T", "F", "TRUE"), ("T", "U", "TRUE"),
    ("F", "T", "TRUE"), ("F", "F", "FALSE"), ("F", "U", "UNKNOWN"),
    ("U", "T", "TRUE"), ("U", "F", "UNKNOWN"), ("U", "U", "UNKNOWN"),
]


@pytest.mark.parametrize("a,b,expected", AND_CASES)
def test_and_truth_table(a, b, expected):
    assert _truth("AND", a, b).value == expected


@pytest.mark.parametrize("a,b,expected", OR_CASES)
def test_or_truth_table(a, b, expected):
    assert _truth("OR", a, b).value == expected


@pytest.mark.parametrize("a,expected", [("T", "FALSE"), ("F", "TRUE"), ("U", "UNKNOWN")])
def test_not_truth_table(a, expected):
    assert _truth("NOT", a).value == expected


# ---- UNKNOWN 传播：数据不可用绝不折叠成命中 ----

def test_unknown_never_folds_to_hit():
    tree = {"op": "AND", "children": [_leaf("a", "a"), _leaf("missing", "missing")]}
    verdict = rc.evaluate_combination(tree, {"a": 1.0})
    assert verdict.truth is rc.TruthValue.UNKNOWN
    assert not verdict.hit
    assert len(verdict.unknown) == 1
    assert verdict.unknown[0].metric == "missing"
    assert verdict.unknown[0].reason

    tree_or = {"op": "OR", "children": [_leaf("f", "f"), _leaf("missing", "missing")]}
    assert not rc.evaluate_combination(tree_or, {"f": -1.0}).hit

    tree_not = {"op": "NOT", "children": [_leaf("missing", "missing")]}
    assert not rc.evaluate_combination(tree_not, {}).hit


def test_unknown_propagates_through_nested():
    tree = {
        "op": "AND",
        "children": [
            {"op": "OR", "children": [_leaf("a", "a"), _leaf("b", "b")]},
            _leaf("missing", "missing"),
        ],
    }
    verdict = rc.evaluate_combination(tree, {"a": 1.0, "b": -1.0})
    assert verdict.truth is rc.TruthValue.UNKNOWN
    assert not verdict.hit


def test_non_numeric_observation_is_unknown():
    for bad in ("5", None, True, float("nan"), float("inf")):
        verdict = rc.evaluate_combination(_leaf("m", "m"), {"m": bad})
        assert verdict.truth is rc.TruthValue.UNKNOWN
        assert not verdict.hit


# ---- 叶子比较符 ----

@pytest.mark.parametrize(
    "operator,value,expected",
    [
        (">", 6.0, True), (">", 5.0, False), (">=", 5.0, True),
        ("<", 4.0, True), ("<", 5.0, False), ("<=", 5.0, True),
        ("==", 5.0, True), ("==", 5.1, False),
    ],
)
def test_leaf_operators(operator, value, expected):
    tree = _leaf("r", "r", threshold=5.0, operator=operator)
    verdict = rc.evaluate_combination(tree, {"r": value})
    assert verdict.hit is expected
    expected_truth = rc.TruthValue.TRUE if expected else rc.TruthValue.FALSE
    assert verdict.truth is expected_truth


# ---- severity 归并 ----

def test_severity_highest_of_matched():
    tree = {"op": "AND", "children": [
        _leaf("a", "a", severity="info"),
        _leaf("b", "b", severity="critical"),
        _leaf("c", "c", severity="warn"),
    ]}
    verdict = rc.evaluate_combination(tree, {"a": 1.0, "b": 1.0, "c": 1.0})
    assert verdict.hit
    assert rc.severity_of(verdict) == "critical"

    # 未命中的 critical 不计入，只取命中叶子里的最高级
    tree2 = {"op": "OR", "children": [
        _leaf("a", "a", severity="warn"),
        _leaf("b", "b", severity="critical"),
    ]}
    verdict2 = rc.evaluate_combination(tree2, {"a": 1.0, "b": -1.0})
    assert rc.severity_of(verdict2) == "warn"


def test_severity_is_info_when_not_hit():
    tree = {"op": "AND", "children": [
        _leaf("a", "a", severity="critical"),
        _leaf("b", "b", severity="critical"),
    ]}
    verdict_false = rc.evaluate_combination(tree, {"a": 1.0, "b": -1.0})
    verdict_unknown = rc.evaluate_combination(tree, {"a": 1.0})
    assert rc.severity_of(verdict_false) == "info"
    assert rc.severity_of(verdict_unknown) == "info"


# ---- 冷却路由 ----

@pytest.mark.parametrize(
    "elapsed,cooldown,expected",
    [(99, 100, False), (100, 100, True), (101, 100, True)],
)
def test_should_emit_boundaries(elapsed, cooldown, expected):
    assert rc.should_emit(1000.0, 1000.0 + elapsed, cooldown, "info") is expected


def test_should_emit_first_time_and_severity_route():
    assert rc.should_emit(None, 500.0, 100.0, "info") is True
    # critical 冷却减半：50 秒即放行，info / warn 仍被拦
    assert rc.should_emit(1000.0, 1050.0, 100.0, "critical") is True
    assert rc.should_emit(1000.0, 1050.0, 100.0, "info") is False
    assert rc.should_emit(1000.0, 1050.0, 100.0, "warn") is False


def test_should_emit_clock_rollback_is_closed():
    assert rc.should_emit(2000.0, 1000.0, 0.0, "critical") is False


@pytest.mark.parametrize("kwargs", [
    {"last_emitted_ts": 0, "now_ts": float("nan"), "cooldown_seconds": 1, "severity": "info"},
    {"last_emitted_ts": 0, "now_ts": 1, "cooldown_seconds": -1, "severity": "info"},
    {"last_emitted_ts": 0, "now_ts": 1, "cooldown_seconds": 1, "severity": "bogus"},
])
def test_should_emit_invalid_args(kwargs):
    with pytest.raises(rc.RuleTreeError):
        rc.should_emit(**kwargs)


# ---- fail-closed：规则树定义非法一律抛清晰异常 ----

def test_invalid_op():
    with pytest.raises(rc.RuleTreeError):
        rc.evaluate_combination(
            {"op": "XOR", "children": [_leaf("a", "a")]}, {"a": 1.0}
        )


@pytest.mark.parametrize("bad_op", ["!=", "=", "=>", "", None])
def test_invalid_operator(bad_op):
    with pytest.raises(rc.RuleTreeError):
        rc.evaluate_combination(_leaf("a", "a", operator=bad_op), {"a": 1.0})


@pytest.mark.parametrize("bad_threshold", ["5", None, True, float("nan"), float("inf")])
def test_non_numeric_threshold(bad_threshold):
    with pytest.raises(rc.RuleTreeError):
        rc.evaluate_combination(
            _leaf("a", "a", threshold=bad_threshold), {"a": 1.0}
        )


def test_depth_exceeded():
    tree = _leaf("a", "a")
    for _ in range(rc.MAX_DEPTH + 2):
        tree = {"op": "AND", "children": [tree]}
    with pytest.raises(rc.RuleTreeError):
        rc.evaluate_combination(tree, {"a": 1.0})


def test_circular_structure():
    tree = {"op": "AND", "children": []}
    tree["children"].append(tree)
    with pytest.raises(rc.RuleTreeError):
        rc.evaluate_combination(tree, {})


@pytest.mark.parametrize("children", [[], "not-a-list", None])
def test_invalid_children(children):
    with pytest.raises(rc.RuleTreeError):
        rc.evaluate_combination({"op": "AND", "children": children}, {})


def test_not_arity():
    with pytest.raises(rc.RuleTreeError):
        rc.evaluate_combination(
            {"op": "NOT", "children": [_leaf("a", "a"), _leaf("b", "b")]},
            {"a": 1.0, "b": 1.0},
        )


def test_invalid_severity():
    with pytest.raises(rc.RuleTreeError):
        rc.evaluate_combination(_leaf("a", "a", severity="fatal"), {"a": 1.0})


def test_observations_must_be_dict():
    with pytest.raises(rc.RuleTreeError):
        rc.evaluate_combination(_leaf("a", "a"), [1, 2, 3])


# ---- to_alert_payload 与 alert_events 字段契约 ----

# 字段来自 alert_events.py 的 upsert_price_observation_events 事件 schema
EXPECTED_FIELDS = {
    "id", "kind", "level", "severity", "symbol", "position_id", "trigger_at",
    "threshold_at", "sellable_now", "action_hint", "t1_note", "basis_json",
    "status", "handled_at", "handled_note", "ignore_streak", "created_at",
    "updated_at", "rule_id", "observation_rule_id", "rule_revision",
    "condition_active", "freq_mode", "last_notified_at", "last_emitted_at",
    "quote_as_of", "quote_source", "user_note", "cooldown_minutes",
}


def _hit_verdict():
    tree = {"op": "AND", "children": [
        _leaf("a", "a", threshold=5.0, severity="info"),
        _leaf("b", "b", threshold=2.0, severity="critical", operator="<"),
    ]}
    return rc.evaluate_combination(tree, {"a": 6.0, "b": 1.0})


def test_to_alert_payload_contract():
    payload = rc.to_alert_payload(
        _hit_verdict(),
        created_at="2026-11-12T10:00:00+08:00",
        rule_id="combo-1",
        symbol="600519",
        source="sqlite_db",
        trigger_at=6.0,
        threshold_at=5.0,
    )
    assert EXPECTED_FIELDS <= set(payload)
    # "无依据不入库" 门禁：basis_json 必须有 rule / actual / evidence 且非空
    assert set(payload["basis_json"]) == {"rule", "actual", "evidence"}
    assert payload["basis_json"]["rule"]
    assert payload["basis_json"]["actual"]
    assert payload["basis_json"]["evidence"]
    assert payload["severity"] == "critical"
    assert payload["severity"] in rc.VALID_SEVERITIES
    assert payload["kind"] == "price_observation"
    assert payload["level"] == "OBSERVATION"
    assert payload["status"] == "OPEN"
    assert payload["rule_id"] == "combo-1"
    assert payload["created_at"] == "2026-11-12T10:00:00+08:00"


def test_to_alert_payload_fail_closed():
    unknown = rc.evaluate_combination(_leaf("m", "m"), {})
    false = rc.evaluate_combination(_leaf("m", "m"), {"m": -1.0})
    for verdict in (unknown, false):
        with pytest.raises(rc.RuleTreeError):
            rc.to_alert_payload(verdict, created_at="2026-11-12T10:00:00+08:00")


# ---- 只读 API ----

@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as test_client:
        yield test_client


URL = "/api/lab/rule-combinators/evaluate"


def test_api_contract_endpoint(client):
    response = client.get("/api/lab/rule-combinators/contract")
    assert response.status_code == 200
    body = response.json()
    assert body["truth_values"] == ["TRUE", "FALSE", "UNKNOWN"]
    assert body["max_depth"] == rc.MAX_DEPTH
    assert "fail_closed" in body
    assert body["alert_payload_fields"]
    assert body["note"].find("不外发") >= 0


def test_api_evaluate_hit(client):
    response = client.post(URL, json={
        "rule_tree": {"op": "AND", "children": [
            _leaf("a", "a", severity="info"),
            _leaf("b", "b", severity="critical"),
        ]},
        "observations": {"a": 1.0, "b": 1.0},
    })
    assert response.status_code == 200
    body = response.json()
    assert body["truth"] == "TRUE"
    assert body["hit"] is True
    assert body["severity"] == "critical"
    assert len(body["matched"]) == 2
    # 调用方未给时间，模块绝不读时钟，也不给发送建议
    assert body["should_emit"] is None


def test_api_evaluate_unknown(client):
    response = client.post(URL, json={
        "rule_tree": {"op": "AND", "children": [
            _leaf("a", "a"), _leaf("missing", "missing"),
        ]},
        "observations": {"a": 1.0},
    })
    assert response.status_code == 200
    body = response.json()
    assert body["truth"] == "UNKNOWN"
    assert body["hit"] is False
    assert body["severity"] == "info"
    assert len(body["unknown"]) == 1


def test_api_evaluate_invalid_tree_returns_422(client):
    response = client.post(URL, json={
        "rule_tree": {"op": "XOR", "children": [_leaf("a", "a")]},
        "observations": {"a": 1.0},
    })
    assert response.status_code == 422


def test_api_evaluate_body_validation(client):
    # rule_tree 不是 dict，pydantic 请求体校验直接 422
    response = client.post(URL, json={"rule_tree": [], "observations": {}})
    assert response.status_code == 422


def test_api_evaluate_should_emit(client):
    response = client.post(URL, json={
        "rule_tree": _leaf("a", "a"),
        "observations": {"a": 1.0},
        "now_ts": 1000.0,
        "last_emitted_ts": 1000.0,
        "cooldown_seconds": 100.0,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["hit"] is True
    assert body["should_emit"] is False


def test_api_evaluate_should_emit_at_boundary(client):
    response = client.post(URL, json={
        "rule_tree": _leaf("a", "a"),
        "observations": {"a": 1.0},
        "now_ts": 1100.0,
        "last_emitted_ts": 1000.0,
        "cooldown_seconds": 100.0,
    })
    assert response.status_code == 200
    assert response.json()["should_emit"] is True


def test_api_is_read_only(client):
    paths = {route.path for route in api.router.routes}
    assert paths == {
        "/api/lab/rule-combinators/contract",
        "/api/lab/rule-combinators/evaluate",
    }
    methods = {method for route in api.router.routes for method in route.methods}
    assert not (methods & {"PUT", "PATCH", "DELETE"})
