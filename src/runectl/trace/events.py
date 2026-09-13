"""The trace event schema (D3).

Every event written to ``trace.jsonl`` is a versioned :class:`Event` envelope
carrying a typed :class:`EventPayload` subclass as ``data``. Each payload class
declares its own ``event_type`` tag, so ``TraceWriter.emit`` only ever accepts a
value from the closed set below — a malformed event is a construction-time
pydantic/mypy error, not something that can slip into the trace as a bare dict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict


class EventPayload(BaseModel):
    """Base for every typed event payload — see ``_ALL_PAYLOADS`` below for the
    current, authoritative count (docs/TRACE.md is the human-readable inventory;
    this docstring used to say "sixteen," which stopped being true as of M6's
    `flag.reviewed` and D19's `budget.exhausted`)."""

    # `extra="ignore"`, not "forbid" (bug found and fixed 2026-09-10): a field
    # removed from a payload class is exactly as valid in an *old* trace as a
    # field that was always there — TraceReader.payload() re-validates every
    # stored line against the *current* schema, and "forbid" turned every past
    # schema change into silent, undated data loss: any run recorded before a
    # field was dropped (artifact_ref, provenance_artifact, timed_out, ...)
    # stopped being readable past its first use of that field, with no error,
    # because TraceReader's own contract is "an unparseable line ends the read
    # silently" (that contract exists for real corruption, e.g. a SIGKILL mid
    # write — it was never meant to fire on an intentional schema edit).
    model_config = ConfigDict(frozen=True, extra="ignore")

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
    # D20 — the *resolved* thinking level (after any provider clamp), so a
    # run's reasoning spend is never invisible after the fact. "off" if
    # thinking wasn't requested. `thinking_clamped_from` is set only when the
    # requested level differs from `thinking_level` — the loud-not-silent
    # record of a degradation, never inferred from `thinking_level` alone.
    thinking_level: str = "off"
    thinking_clamped_from: str | None = None


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


class LlmThinking(EventPayload):
    """The model's reasoning for one step (D20, added 2026-09-09).

    A separate event rather than a field on ``llm.response``: thinking text is
    long and unbounded, so keeping it as its own event lets the writer's 8 KB
    artifact-spill rule apply to it independently — a long thinking block
    doesn't bloat every ``llm.response`` line in ``trace.jsonl``. Only emitted
    when thinking was requested and the provider actually returned text (an
    empty ``thinking_text`` from the provider emits nothing, since there is
    nothing to show — see ``loop/runner.py``).
    """

    event_type: ClassVar[str] = "llm.thinking"

    step: int
    text: str
    level: str
    truncated: bool = False


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


class FlagCandidate(EventPayload):
    """Carries ``provenance`` (D15 §1) from the skeleton onward, even though the
    M4 judge is minimal — this is the seam M6's judge is built against."""

    event_type: ClassVar[str] = "flag.candidate"

    step: int
    flag: str
    how_found: str
    provenance_seq: int


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


class FlagRederived(EventPayload):
    """D15 mechanism 2 re-ran the cited command in the sandbox, and this is what
    came back (added 2026-09-10 — D3's event set amended from 19 to 20).

    This exists because the trace was **incomplete without it**: re-derivation
    calls ``Sandbox.exec`` directly rather than through the dispatcher, so a
    command really executed in the run's container and left no record. That is
    the defect. A replay desyncing (``ReplaySandbox`` serves recorded exec
    results strictly in order, so an unrecorded exec silently consumed the next
    tool call's output) was only the symptom that surfaced it.

    Carries the whole ``ExecResult`` because that is what makes it replayable:
    ``sandbox.replay.exec_results_from_trace`` reads these back alongside
    ``tool.result`` to keep the queue aligned. Emitted by the runner, never by
    the judge — the judge returns verdicts and mutates nothing (D6).

    ``errored`` marks the case where ``exec`` raised instead of returning. The
    verdict is the same either way ("could not re-derive"), and a replay of an
    errored re-derivation is served a plain ``ok=False`` result rather than a
    re-raised exception — a different mechanism reaching the same verdict, which
    is what keeps the cursor aligned.
    """

    event_type: ClassVar[str] = "flag.rederived"

    step: int
    source_seq: int
    command: str
    matched: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float
    truncated: bool = False
    errored: bool = False


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
    LlmThinking,
    ToolCall,
    ToolResultEvent,
    ProgressScored,
    BudgetBlocked,
    StrategyShift,
    FlagCandidate,
    FlagReviewed,
    FlagRederived,
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

    def payload(self) -> EventPayload:
        """Validate ``data`` back into its typed payload model.

        Spilled fields are substituted with a short placeholder first; see
        :func:`describe_artifact_refs`. Without that, any event carrying a tool
        output over the spill threshold raised a ``ValidationError`` here — a
        string field holding ``{"$artifact": ..., "bytes": ...}`` is not a
        string — and every caller of this method broke on it.
        """
        model = EVENT_TYPES.get(self.type)
        if model is None:
            raise ValueError(f"unknown event type: {self.type!r}")
        return model.model_validate(describe_artifact_refs(self.data))


def _is_artifact_ref(value: Any) -> bool:
    return isinstance(value, dict) and "$artifact" in value and "bytes" in value


def describe_artifact_refs(data: Any) -> Any:
    """Replace unresolved spill references with a short human-readable note.

    The writer moves any string over ``ARTIFACT_SPILL_THRESHOLD_BYTES`` into
    ``artifacts/`` and leaves ``{"$artifact": digest, "bytes": n}`` in its place
    (D3). That is correct on disk and wrong in memory: the payload models type
    those fields as ``str``, so validating a spilled event raised instead of
    returning one.

    Callers that can reach the artifacts directory should resolve the real
    content first (``TraceReader`` does). This is the fallback for the ones that
    cannot — chiefly the live render path, which receives an event at emit time
    and only ever prints a clipped line of it anyway.
    """
    if _is_artifact_ref(data):
        return f"<{data['bytes']} bytes in artifacts/{data['$artifact']}.txt>"
    if isinstance(data, dict):
        return {k: describe_artifact_refs(v) for k, v in data.items()}
    if isinstance(data, list):
        return [describe_artifact_refs(v) for v in data]
    return data


def resolve_artifact_refs(data: Any, artifacts_dir: Path) -> Any:
    """Restore spilled strings from ``artifacts/``, so a reader sees the run as
    it happened rather than a summary of it. Falls back to the placeholder for
    an artifact file that is missing (a partially copied run directory)."""
    if _is_artifact_ref(data):
        path = artifacts_dir / f"{data['$artifact']}.txt"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return describe_artifact_refs(data)
    if isinstance(data, dict):
        return {k: resolve_artifact_refs(v, artifacts_dir) for k, v in data.items()}
    if isinstance(data, list):
        return [resolve_artifact_refs(v, artifacts_dir) for v in data]
    return data
