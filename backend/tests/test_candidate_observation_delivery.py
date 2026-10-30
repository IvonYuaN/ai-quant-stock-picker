from __future__ import annotations

import candidate_observation_delivery as delivery
import candidate_observations as store


def _item(revision: int = 2) -> dict:
    return {
        "id": "candidate-1",
        "symbol": "600519",
        "name": "贵州茅台",
        "price": 1500.0,
        "comparator": "at_or_above",
        "condition": "放量后复核趋势",
        "invalidation": "跌回区间下沿则失效",
        "state": "active",
        "revision": revision,
    }


def test_candidate_observation_delivery_deduplicates_same_adopted_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_file", lambda: str(tmp_path / "candidate_observations.json"))
    calls: list[str] = []

    import aqsp.notification_runtime as runtime

    from aqsp.notifier import NotifyResult

    def dispatch(markdown: str, **kwargs):
        del markdown
        calls.append(kwargs["prefix"])
        return [NotifyResult("synthetic", True, "ok")]

    monkeypatch.setattr(runtime, "dispatch_notification", dispatch)
    check = [{"symbol": "600519", "price": 1501.0, "quote_as_of": "2026-10-30T10:00:00+08:00", "triggered": True}]
    first = delivery.deliver_triggered([_item()], check)
    second = delivery.deliver_triggered([_item()], check)
    assert first == (["candidate-observation: 候选观察触发通知已发送"], False)
    assert second == ([], True)
    assert calls == ["candidate-observation"]

    next_revision = delivery.deliver_triggered([_item(3)], check)
    assert next_revision[0] == ["candidate-observation: 候选观察触发通知已发送"]
    assert calls == ["candidate-observation", "candidate-observation"]
    state = (tmp_path / "notify-state-candidate-observation.json").read_text(encoding="utf-8")
    assert '"candidate-observation|candidate-1|2"' in state
    assert '"candidate-observation|candidate-1|3"' in state


def test_candidate_observation_delivery_skips_unknown_or_non_triggered_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_file", lambda: str(tmp_path / "candidate_observations.json"))
    import aqsp.notification_runtime as runtime

    monkeypatch.setattr(runtime, "dispatch_notification", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not send")))
    result = delivery.deliver_triggered(
        [_item()],
        [
            {"symbol": "600519", "triggered": False},
            {"symbol": "000001", "triggered": True},
        ],
    )
    assert result == ([], False)
