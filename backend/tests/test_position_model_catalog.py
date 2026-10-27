"""Provider iteration is discovered, never silently selected or credentialed."""
from __future__ import annotations

import asyncio
import json

import httpx

from position_discussion import ModelEndpoint
import position_model_catalog as catalog


def test_model_catalog_marks_old_model_when_provider_replaces_its_directory(tmp_path, monkeypatch) -> None:
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "agnes-3.0-flash"}]})
    client_class = httpx.AsyncClient
    monkeypatch.setattr(catalog.httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw))
    endpoint = ModelEndpoint("https://model.invalid/v1/chat/completions", "private-key", "agnes-2.0-flash")
    result = asyncio.run(catalog.read_catalog(endpoint, tmp_path))
    assert result["listed"] is False
    assert result["current_model"] == "agnes-2.0-flash"
    assert result["models"] == ["agnes-3.0-flash"]
    assert seen[0].method == "GET" and seen[0].url.path == "/v1/models"
    asyncio.run(catalog.read_catalog(endpoint, tmp_path))
    asyncio.run(catalog.read_catalog(endpoint, tmp_path, refresh=True))
    assert len(seen) == 1  # Refresh floor also prevents repeated discovery clicks.
    assert "private-key" not in json.dumps(result)
    assert all("private-key" not in p.read_text() for p in tmp_path.rglob("*.json"))


def test_model_catalog_does_not_claim_model_retired_when_directory_fails(tmp_path, monkeypatch) -> None:
    client_class = httpx.AsyncClient
    monkeypatch.setattr(catalog.httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(lambda req: httpx.Response(401, text="private-key")), **kw))
    result = asyncio.run(catalog.read_catalog(ModelEndpoint("https://model.invalid/v1/chat/completions", "private-key", "model"), tmp_path))
    assert result["listed"] is None and "HTTP 401" in result["issue"]
    assert "private-key" not in json.dumps(result)


def test_model_catalog_bounds_reply_when_upstream_returns_oversized_data(tmp_path, monkeypatch) -> None:
    client_class = httpx.AsyncClient
    monkeypatch.setattr(catalog.httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * 101)), **kw))
    result = asyncio.run(catalog.read_catalog(ModelEndpoint("https://model.invalid/v1/chat/completions", "key", "model"), tmp_path,
                                            limits=catalog.CatalogLimits(max_response_bytes=100)))
    assert result["listed"] is None and "上限" in result["issue"]


def test_model_catalog_keeps_missing_unknown_when_directory_is_truncated(tmp_path, monkeypatch) -> None:
    client_class = httpx.AsyncClient
    monkeypatch.setattr(catalog.httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"data": [{"id": "a"}, {"id": "z"}]})), **kw))
    result = asyncio.run(catalog.read_catalog(ModelEndpoint("https://model.invalid/v1/chat/completions", "key", "z"), tmp_path,
                                            limits=catalog.CatalogLimits(max_models=1)))
    assert result["listed"] is None and not result["directory_complete"]
