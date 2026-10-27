"""Candidate observation HTTP boundary, installed by the application owner."""
from __future__ import annotations

from typing import Any, Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from aqsp.core.errors import DataError
import candidate_observation as resources
import candidate_discussion as discussion

router = APIRouter(prefix='/api/candidate-observations', tags=['候选策略观察'])


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ModelConfig(Input):
    provider: str
    baseURL: str
    apiKey: str
    model: str


class DiscussionInput(Input):
    expected_version: int = Field(ge=0, strict=True)
    expected_evidence_version: str = Field(min_length=1)
    llm: ModelConfig | None = None
    server_model: str | None = Field(default=None, min_length=1, max_length=200)


class AdoptInput(Input):
    expected_version: int = Field(ge=0, strict=True)
    expected_evidence_version: str = Field(min_length=1)
    discussion_id: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)
    choice: Literal['pullback', 'confirmation', 'defer']
    action_index: int | None = Field(default=None, ge=0, strict=True)
    comparator: Literal['at_or_above', 'at_or_below'] | None = None
    operation_id: str = Field(min_length=1, max_length=128)


class StopInput(Input):
    expected_version: int = Field(ge=0, strict=True)
    operation_id: str = Field(min_length=1, max_length=128)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, DataError):
        return HTTPException(503, str(exc))
    return HTTPException(409 if isinstance(exc, resources.CandidateConflict) else 400, str(exc))


@router.get('')
def list_candidates() -> dict[str, Any]:
    """Persisted states for the current tenant; no market/model refresh."""
    try:
        return {'data': resources.list_workspaces()}
    except (ValueError, DataError) as exc:
        raise _error(exc) from exc


@router.get('/{symbol}')
def candidate_detail(symbol: str) -> dict[str, Any]:
    """Current research plus one quote and the saved observation."""
    try:
        return {'data': resources.workspace(symbol)}
    except (ValueError, DataError) as exc:
        raise _error(exc) from exc


@router.post('/{symbol}/discussion')
async def discuss_candidate(symbol: str, body: DiscussionInput) -> dict[str, Any]:
    """Explicit model work, with no automatic candidate intent adoption."""
    try:
        return {'data': await discussion.run_discussion(symbol, body.expected_version, body.expected_evidence_version,
                        body.llm.model_dump() if body.llm else None, body.server_model)}
    except (ValueError, DataError) as exc:
        raise _error(exc) from exc


@router.post('/{symbol}/strategy-evidence')
async def refresh_candidate_sources(symbol: str) -> dict[str, Any]:
    """Refresh public market/sector evidence without adopting or creating a plan."""
    try:
        return {'data': await resources.refresh_sources(symbol)}
    except (ValueError, DataError) as exc:
        raise _error(exc) from exc


@router.post('/{symbol}/discussion/stream')
async def stream_candidate(symbol: str, body: DiscussionInput) -> StreamingResponse:
    """Request-local NDJSON; the generator owns cancellation and cleanup."""
    try:
        resources.symbol(symbol)
    except ValueError as exc:
        raise _error(exc) from exc
    return StreamingResponse(discussion.stream_discussion(symbol, body.expected_version, body.expected_evidence_version,
                             body.llm.model_dump() if body.llm else None, body.server_model), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@router.post('/{symbol}/adopt')
def adopt_candidate(symbol: str, body: AdoptInput) -> dict[str, Any]:
    """Human route/comparator confirmation; never a buy order."""
    try:
        return {'data': resources.adopt(symbol, body.model_dump())}
    except (ValueError, DataError) as exc:
        raise _error(exc) from exc


@router.post('/{symbol}/stop')
def stop_candidate(symbol: str, body: StopInput) -> dict[str, Any]:
    """Stop only this candidate observation, with retry-safe operation identity."""
    try:
        return {'data': resources.stop(symbol, body.model_dump())}
    except (ValueError, DataError) as exc:
        raise _error(exc) from exc
