"""The trace event schema (D3, plan §1.1).

Every event written to ``trace.jsonl`` is a versioned :class:`Event` envelope
carrying a typed :class:`EventPayload` subclass as ``data``. Each payload class
declares its own ``event_type`` tag, so ``TraceWriter.emit`` only ever accepts a
value from the closed set below — a malformed event is a construction-time
pydantic/mypy error, not something that can slip into the trace as a bare dict.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict


class EventPayload(BaseModel):
    """Base for all sixteen typed event payloads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: ClassVar[str] = ""


class RunStarted(EventPayload):
    event_type: ClassVar[str] = "run.started"

    challenge_name: str
    category: str
    model: str
    provider: str
    approval_policy: str
    max_steps: int
    network: str


class ChallengeLoaded(EventPayload):
    event_type: ClassVar[str] = "challenge.loaded"

    name: str
    category: str
    description_chars: int
    file_count: int
    flag_format: str | None = None


class TriageResult(EventPayload):
    event_type: ClassVar[str] = "triage.result"

    category: str
    commands: list[str]
    findings: dict[str, str]


class LlmRequest(EventPayload):
    event_type: ClassVar[str] = "llm.request"

    step: int
    model: str
    provider: str
    message_count: int
    cached_prefix: bool


class ToolCallSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    arguments: dict[str, Any]


class LlmResponse(EventPayload):
    event_type: ClassVar[str] = "llm.response"

    step: int
    model: str
    provider: str
    stop_reason: str
    text_chars: int
    tool_call: ToolCallSummary | None = None
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ToolCall(EventPayload):
    event_type: ClassVar[str] = "tool.call"

    step: int
    tool: str
    arguments: dict[str, Any]


class ToolResultEvent(EventPayload):
    event_type: ClassVar[str] = "tool.result"

    step: int
    tool: str
    ok: bool
    kind: Literal["output", "error", "blocked"]
    stdout: str
    stderr: str
    exit_code: int | None = None
    duration_s: float
    truncated: bool
    artifact_ref: str | None = None


class ProgressScored(EventPayload):
    """M5 fills this in for real; the skeleton's no-op stub still emits it (D16 seam)."""

    event_type: ClassVar[str] = "progress.scored"

    step: int
    family: str
    fingerprint: str
    delta: float
    signal: Literal["none", "low", "high"]


class BudgetBlocked(EventPayload):
    event_type: ClassVar[str] = "budget.blocked"

    step: int
    family: str
    reason: str


class StrategyShift(EventPayload):
    event_type: ClassVar[str] = "strategy.shift"

    step: int
    reason: str
    evidence_summary: str


class EvidenceAdded(EventPayload):
    event_type: ClassVar[str] = "evidence.added"

    step: int
    kind: str
    summary: str
    source_seq: int | None = None


class FlagCandidate(EventPayload):
    """Carries ``provenance`` (D15 §1) from the skeleton onward, even though the
    M4 judge is minimal — this is the seam M6's judge is built against."""

    event_type: ClassVar[str] = "flag.candidate"

    step: int
    flag: str
    how_found: str
    provenance_seq: int
    provenance_artifact: str | None = None


class FlagReviewed(EventPayload):
    """The D15 disconfirmation pass's verdict on one candidate.

    Its own event rather than a line in `flag.decision`'s reason, because the
    point of the pass is that a human can read why a model doubted a flag.
    """

    event_type: ClassVar[str] = "flag.reviewed"

    step: int
    flag: str
    sound: bool
    reason: str


class FlagDecision(EventPayload):
    event_type: ClassVar[str] = "flag.decision"

    step: int
    flag: str
    decision: Literal["finalized", "pending", "rejected"]
    reason: str


class CostUpdated(EventPayload):
    event_type: ClassVar[str] = "cost.updated"

    step: int | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cumulative_cost_usd: float


class BudgetExhausted(EventPayload):
    """The run hit its spend ceiling and stopped itself (D19)."""

    event_type: ClassVar[str] = "budget.exhausted"

    step: int
    limit_usd: float
    spent_usd: float


class ErrorEvent(EventPayload):
    event_type: ClassVar[str] = "error"

    step: int | None
    kind: str
    message: str
    recoverable: bool


class RunFinished(EventPayload):
    event_type: ClassVar[str] = "run.finished"

    outcome: Literal["solved", "candidate", "exhausted", "error"]
    flag: str | None = None
    steps_used: int
    progress_steps: int
    blocked_steps: int
    cost_usd: float
    duration_s: float
    exit_code: int


_ALL_PAYLOADS: tuple[type[EventPayload], ...] = (
    RunStarted,
    ChallengeLoaded,
    TriageResult,
    LlmRequest,
    LlmResponse,
    ToolCall,
    ToolResultEvent,
    ProgressScored,
    BudgetBlocked,
    StrategyShift,
    EvidenceAdded,
    FlagCandidate,
    FlagReviewed,
    FlagDecision,
    BudgetExhausted,
    CostUpdated,
    ErrorEvent,
    RunFinished,
)

EVENT_TYPES: dict[str, type[EventPayload]] = {cls.event_type: cls for cls in _ALL_PAYLOADS}


class Event(BaseModel):
    """The versioned envelope written to ``trace.jsonl``, one JSON object per line."""

    model_config = ConfigDict(frozen=True)

    v: Literal[1] = 1
    run_id: str
    seq: int
    ts: float
    type: str
    data: dict[str, Any]

    @classmethod
    def from_payload(cls, *, run_id: str, seq: int, ts: float, payload: EventPayload) -> Event:
        return cls(
            run_id=run_id,
            seq=seq,
            ts=ts,
            type=type(payload).event_type,
            data=payload.model_dump(mode="json"),
        )

    def payload(self) -> EventPayload:
        """Validate ``data`` back into its typed payload model."""
        model = EVENT_TYPES.get(self.type)
        if model is None:
            raise ValueError(f"unknown event type: {self.type!r}")
        return model.model_validate(self.data)
