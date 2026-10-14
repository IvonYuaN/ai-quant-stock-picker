"""API 验证/契约测（FastAPI TestClient）。大多在校验层就返回，不联网、可靠。"""

import pytest
from fastapi.testclient import TestClient

import app as app_module

client = TestClient(app_module.app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.parametrize(
    "path",
    [
        "/api/quote?codes=abc",
        "/api/valuation?code=12",
        "/api/margin?code=notcode",
        "/api/holders?code=1234567",
        "/api/announcements?code=",
    ],
)
def test_bad_code_400(path):
    assert client.get(path).status_code == 400


def test_industry_top_range():
    assert client.get("/api/industry?top=2").status_code == 422  # ge=5
    assert client.get("/api/industry?top=999").status_code == 422  # le=50


def test_chat_empty_messages_400():
    r = client.post(
        "/api/chat",
        json={
            "messages": [],
            "llm": {"model": "x", "baseURL": "http://x", "apiKey": "k"},
        },
    )
    assert r.status_code == 400


def test_chat_api_missing_key_400():
    # API 接入缺 baseURL/apiKey → 400（在开流前拦下）
    r = client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "llm": {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "baseURL": "",
                "apiKey": "",
            },
        },
    )
    assert r.status_code == 400


def test_chat_cli_not_installed_400():
    # 订阅接入选一个本机没装的 CLI → 400 明确提示（不静默失败）
    r = client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "llm": {
                "provider": "cli-qwen",
                "model": "qwen-code",
                "baseURL": "",
                "apiKey": "",
            },
        },
    )
    # qwen 一般未装 → 400；若恰好装了 qwen 则会进流式（放宽断言）
    assert r.status_code in (400, 200)


@pytest.mark.parametrize(
    "path",
    [
        "/api/portfolio",
        "/api/chat",
        "/api/radar/refresh",
    ],
)
def test_public_mode_requires_api_key_for_private_and_expensive_routes(
    monkeypatch, path
):
    monkeypatch.setattr(app_module, "_PUBLIC_MODE", True)
    monkeypatch.setattr(app_module, "_API_KEY", "")
    response = (
        client.get(path) if path == "/api/portfolio" else client.post(path, json={})
    )
    assert response.status_code == 503


@pytest.mark.parametrize(
    "path",
    [
        "/api/portfolio",
        "/api/chat",
        "/api/radar/refresh",
    ],
)
def test_public_mode_authenticates_private_and_expensive_routes(monkeypatch, path):
    monkeypatch.setattr(app_module, "_PUBLIC_MODE", True)
    monkeypatch.setattr(app_module, "_API_KEY", "sekret")
    response = (
        client.get(path) if path == "/api/portfolio" else client.post(path, json={})
    )
    assert response.status_code == 401


def test_public_cors_does_not_allow_wildcard():
    assert app_module._cors_origins("*", public_mode=True) == []
    assert app_module._cors_origins("https://app.example, *", public_mode=True) == [
        "https://app.example"
    ]
    assert app_module._cors_origins(None, public_mode=False) == ["*"]


def test_global_stock_404(monkeypatch):
    """无法解析的美股/港股代码 → 404（不 500、不崩）。"""
    import gstock

    monkeypatch.setattr(gstock, "us_hk_stock", lambda q: {})
    assert client.get("/api/global/stock?symbol=ZZZZ").status_code == 404


def test_gstock_quote_full_null_shape():
    """行情取不到时 `_quote_from({})` 仍返回完整 null 形状（契合 GlobalQuote 类型），不是空 dict。"""
    import gstock

    q = gstock._quote_from({})
    assert set(q) == {
        "code",
        "name",
        "price",
        "open",
        "high",
        "low",
        "prev_close",
        "amount",
        "mcap",
        "change_pct",
    }
    assert all(v is None for v in q.values())


def test_gate_freshness_maps_real_gate_fields(tmp_path, monkeypatch):
    """#380 守卫：``_gate_freshness`` 必须映射 gate JSON 的**真实**字段。

    事故（2026-10-14 实测 prod）：gate JSON 由 ``walkforward_gate.py`` 写入
    ``run_date``/``both_pass``/``dsr_pass``/``pbo_pass``/``deflated_sharpe``/``pbo``/
    ``n_variants`` —— **没有** ``verdict``/``generated_at``/``updated``。旧实现直接读这三个
    键 ⇒ 文件存在即 present=true 而三字段**恒 null**（/api/health 的 gate 区块信息量为零）。
    """
    import json as _json

    gate = tmp_path / "walkforward_gate.json"
    gate.write_text(
        _json.dumps(
            {
                "run_date": "2026-10-03",
                "both_pass": False,
                "dsr_pass": False,
                "pbo_pass": True,
                "deflated_sharpe": -1.4354,
                "pbo": 0.4762,
                "n_variants": 8,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        app_module, "_gate_freshness_candidates", lambda: [str(gate)]
    )

    out = app_module._gate_freshness()

    assert out["present"] is True
    # 核心：三字段不再恒 null（旧实现的 bug）
    assert out["verdict"] == "fail"  # verdict ← both_pass
    assert out["generated_at"] == "2026-10-03"  # generated_at ← run_date
    assert isinstance(out["updated"], str) and "T" in out["updated"]  # ← 文件 mtime
    # 原始判据可排障
    assert out["both_pass"] is False
    assert out["pbo_pass"] is True
    assert out["deflated_sharpe"] == -1.4354
    assert out["n_variants"] == 8


def test_gate_freshness_absent_file_returns_not_present(tmp_path, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "_gate_freshness_candidates",
        lambda: [str(tmp_path / "nope.json")],
    )
    out = app_module._gate_freshness()
    assert out == {"present": False, "verdict": None, "generated_at": None, "updated": None}
