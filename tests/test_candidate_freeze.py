from __future__ import annotations

from datetime import datetime
import json

import pytest

from aqsp.core.time import SHANGHAI_TZ
from aqsp.core.types import PickResult
from aqsp.services import candidate_freeze


SESSION = "2026-10-23"
NOW = datetime(2026, 10, 24, 9, 0, tzinfo=SHANGHAI_TZ)


def _pick(symbol: str = "600000", *, signal_date: str = SESSION) -> PickResult:
    return PickResult(
        symbol=symbol,
        name="浦发银行",
        date=signal_date,
        close=10.2,
        score=75.0,
        rating="B",
        entry_type="观察",
        ideal_buy=10.1,
        stop_loss=9.6,
        take_profit=11.0,
        position="10%",
        strategies=("momentum",),
        reasons=("量价配合",),
        risks=(),
        metrics={"strategy_weight_snapshot": {"version": "test"}},
    )


@pytest.fixture(autouse=True)
def _freeze_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(candidate_freeze, "today_shanghai", lambda: NOW.date())
    monkeypatch.setattr(candidate_freeze, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(candidate_freeze, "_current_trading_session_date", lambda: SESSION)


def _freeze(path, picks: list[PickResult] | None = None):
    return candidate_freeze.freeze_scored_candidates(
        session_date=SESSION,
        request_fingerprint="fingerprint",
        source="fixture",
        data_latest_trade_date=SESSION,
        thresholds_version="v-test",
        regime="trend",
        strategy_weights={"momentum": 1.0},
        resolved_symbols=["600000", "000001"],
        min_price=1.0,
        max_price=100.0,
        min_avg_amount=1_000_000.0,
        picks=[_pick()] if picks is None else picks,
        path=path,
    )


def test_candidate_freeze_reuses_empty_same_session_snapshot(tmp_path) -> None:
    path = tmp_path / "candidate.jsonl"
    first = _freeze(path, [])
    loaded = candidate_freeze.load_candidate_snapshot(SESSION, "fingerprint", path)
    assert first.screened_picks == ()
    assert loaded is not None
    assert loaded.screened_picks == ()


def test_candidate_freeze_rejects_corrupted_snapshot_before_recompute(tmp_path) -> None:
    path = tmp_path / "candidate.jsonl"
    _freeze(path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["screened_picks"][0]["score"] = 99.0
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(candidate_freeze.CandidateSnapshotError, match="校验"):
        candidate_freeze.load_candidate_snapshot(SESSION, "fingerprint", path)


def test_candidate_freeze_rejects_configuration_identity_conflict(tmp_path) -> None:
    path = tmp_path / "candidate.jsonl"
    _freeze(path)
    with pytest.raises(candidate_freeze.CandidateSnapshotError, match="配置身份冲突"):
        candidate_freeze.load_candidate_snapshot(SESSION, "other", path)


def test_candidate_freeze_rejects_future_market_date_and_incomplete_pick(tmp_path) -> None:
    path = tmp_path / "candidate.jsonl"
    with pytest.raises(candidate_freeze.CandidateSnapshotError, match="行情日期"):
        candidate_freeze.freeze_scored_candidates(
            session_date=SESSION,
            request_fingerprint="fingerprint",
            source="fixture",
            data_latest_trade_date="2026-10-24",
            thresholds_version="v-test",
            regime="trend",
            strategy_weights={"momentum": 1.0},
            resolved_symbols=["600000"],
            min_price=1.0,
            max_price=100.0,
            min_avg_amount=1.0,
            picks=[_pick()],
            path=path,
        )
    with pytest.raises(candidate_freeze.CandidateSnapshotError, match="名称"):
        candidate_freeze.freeze_scored_candidates(
            session_date=SESSION,
            request_fingerprint="other-fingerprint",
            source="fixture",
            data_latest_trade_date=SESSION,
            thresholds_version="v-test",
            regime="trend",
            strategy_weights={"momentum": 1.0},
            resolved_symbols=["600000"],
            min_price=1.0,
            max_price=100.0,
            min_avg_amount=1.0,
            picks=[_pick().__class__(**{**_pick().__dict__, "name": ""})],
            path=path,
        )


def test_candidate_freeze_rejects_historical_increment(tmp_path) -> None:
    with pytest.raises(candidate_freeze.CandidateSnapshotError, match="当前交易日"):
        candidate_freeze.record_candidate_increment(
            candidate_freeze.CandidateIncrement(
                session_date="2026-10-22",
                observed_at=NOW.isoformat(),
                request_fingerprint="fingerprint",
                symbol="600000",
                status="invalidated",
                reason="停牌",
            ),
            tmp_path / "increments.jsonl",
        )
