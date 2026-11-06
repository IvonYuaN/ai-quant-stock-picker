from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import research_variant_decisions as decisions
import tenant
from aqsp.web.home_snapshot import HomeSnapshotVariant, HomeSnapshotVariantSuite


@pytest.fixture
def current_artifact(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(decisions.pf, "CACHE_DIR", str(tmp_path))
    variant = HomeSnapshotVariant(
        variant_id="variant-a",
        label="低波动组",
        initial_cash=100000,
        cash=80000,
        final_equity=105000,
        total_pnl=5000,
        return_pct=0.05,
        filled_orders=4,
        rejected_orders=1,
        start_date="2025-01-01",
        end_date="2025-12-31",
        data_mode="point_in_time",
        strategy="low_vol_trend",
        independent_signal_days=36,
        lifecycle_status="继续观察",
        lifecycle_reason="自动研究分层",
    )
    snapshot = SimpleNamespace(
        variant_suite=HomeSnapshotVariantSuite(
            generated_at="2026-11-01T18:00:00+08:00",
            batch_id="batch-1",
        ),
        variants=(variant,),
    )
    monkeypatch.setattr(decisions.aqsp_bridge, "load_surface", lambda: SimpleNamespace(current=snapshot))
    token = tenant.current_tenant.set("local")
    yield snapshot
    tenant.current_tenant.reset(token)


def test_variant_review_records_append_only_decisions_with_revision(current_artifact):
    initial = decisions.list_reviews()
    artifact = initial["artifacts"][0]

    first = decisions.record_review(
        artifact_id=artifact["artifact_id"], batch_id="batch-1", variant_id="variant-a",
        choice="continue_validation", note="补做独立样本外验证", expected_revision=0,
    )
    second = decisions.record_review(
        artifact_id=artifact["artifact_id"], batch_id="batch-1", variant_id="variant-a",
        choice="defer", note="当前证据不足，先暂缓", expected_revision=1,
    )

    result = decisions.list_reviews()
    assert first["revision"] == 1
    assert second["revision"] == 2
    assert result["artifacts"][0]["latest_decision"]["id"] == second["id"]
    assert [event["id"] for event in decisions.list_events()] == [second["id"], first["id"]]
    assert decisions.list_events()[0]["variant"]["strategy"] == "low_vol_trend"


def test_variant_review_is_idempotent_for_repeated_same_submission(current_artifact):
    artifact = decisions.list_reviews()["artifacts"][0]
    first = decisions.record_review(
        artifact_id=artifact["artifact_id"], batch_id="batch-1", variant_id="variant-a",
        choice="reject", note="不继续研究", expected_revision=0,
    )
    repeated = decisions.record_review(
        artifact_id=artifact["artifact_id"], batch_id="batch-1", variant_id="variant-a",
        choice="reject", note="不继续研究", expected_revision=0,
    )
    assert repeated == first
    assert len(decisions.list_events()) == 1
    assert "variant" not in decisions.list_reviews()["artifacts"][0]


def test_variant_review_rejects_stale_artifact_and_revision(current_artifact):
    artifact = decisions.list_reviews()["artifacts"][0]
    with pytest.raises(decisions.VariantReviewConflict, match="快照已更新"):
        decisions.record_review(
            artifact_id=artifact["artifact_id"], batch_id="old-batch", variant_id="variant-a",
            choice="defer", note="先等", expected_revision=0,
        )

    decisions.record_review(
        artifact_id=artifact["artifact_id"], batch_id="batch-1", variant_id="variant-a",
        choice="defer", note="先等", expected_revision=0,
    )
    with pytest.raises(decisions.VariantReviewConflict, match="已被更新"):
        decisions.record_review(
            artifact_id=artifact["artifact_id"], batch_id="batch-1", variant_id="variant-a",
            choice="reject", note="不继续", expected_revision=0,
        )


def test_variant_review_rejects_artifact_changed_after_read(current_artifact):
    artifact = decisions.list_reviews()["artifacts"][0]
    snapshot = current_artifact
    changed_variant = replace(snapshot.variants[0], return_pct=0.08)
    snapshot.variants = (changed_variant,)

    with pytest.raises(decisions.VariantReviewConflict, match="快照已更新"):
        decisions.record_review(
            artifact_id=artifact["artifact_id"], batch_id="batch-1", variant_id="variant-a",
            choice="continue_validation", note="补独立 OOS", expected_revision=0,
        )


def test_variant_review_keeps_tenants_separate(current_artifact):
    local_artifact = decisions.list_reviews()["artifacts"][0]
    decisions.record_review(
        artifact_id=local_artifact["artifact_id"], batch_id="batch-1", variant_id="variant-a",
        choice="defer", note="本地用户决定", expected_revision=0,
    )
    token = tenant.current_tenant.set("u_other")
    try:
        other = decisions.list_reviews()
        assert other["artifacts"][0]["latest_decision"] is None
        assert decisions.list_events() == []
    finally:
        tenant.current_tenant.reset(token)


def test_variant_review_fails_closed_on_corrupt_store(current_artifact, tmp_path: Path):
    path = tmp_path / "research_variant_decisions.json"
    path.write_text(json.dumps({"schema_version": 2, "events": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="格式无效"):
        decisions.list_reviews()


def test_variant_review_returns_empty_list_when_no_variant_batch_exists(current_artifact):
    current_artifact.variants = ()
    current_artifact.variant_suite = HomeSnapshotVariantSuite()

    result = decisions.list_reviews()

    assert result == {"batch_id": "", "generated_at": "", "artifacts": []}


def test_variant_review_rejects_variants_without_batch_identity(current_artifact):
    current_artifact.variant_suite = HomeSnapshotVariantSuite(generated_at="2026-11-01T18:00:00+08:00")

    with pytest.raises(decisions.VariantReviewUnavailable, match="无法核对"):
        decisions.list_reviews()


def test_variant_review_api_returns_empty_list_for_cold_start(current_artifact):
    from fastapi.testclient import TestClient
    import app as app_module

    current_artifact.variants = ()
    current_artifact.variant_suite = HomeSnapshotVariantSuite()

    response = TestClient(app_module.app).get("/api/research/variant-reviews")

    assert response.status_code == 200
    assert response.json()["data"] == {"batch_id": "", "generated_at": "", "artifacts": []}


def test_variant_review_api_validates_snapshot_and_revision(current_artifact):
    from fastapi.testclient import TestClient
    import app as app_module

    client = TestClient(app_module.app)
    listing = client.get("/api/research/variant-reviews")
    assert listing.status_code == 200
    artifact = listing.json()["data"]["artifacts"][0]
    payload = {
        "artifact_id": artifact["artifact_id"], "batch_id": "batch-1", "variant_id": "variant-a",
        "choice": "continue_validation", "note": "补做独立样本外验证", "expected_revision": 0,
    }
    saved = client.post("/api/research/variant-reviews", json=payload)
    assert saved.status_code == 200
    assert saved.json()["data"]["revision"] == 1

    stale = client.post("/api/research/variant-reviews", json={**payload, "choice": "defer", "note": "暂缓"})
    assert stale.status_code == 409

    invalid = client.post("/api/research/variant-reviews", json={**payload, "choice": "promote_to_live"})
    assert invalid.status_code == 422
