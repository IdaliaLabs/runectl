"""stdout=NDJSON (off-TTY default); stderr=human render (D4, D13, plan §9.2).

Each renderer is a pure function of one event — it may never read run state
(D4), which is what guarantees the human view can only ever show you something
the trace actually contains.

The human render exists to be watched live while an agent works, so it is one
short line per event: what it did, what came back, what it cost. Full command
text and full output are in ``trace.jsonl``; this is the readable surface over
it, not a replacement for it.
"""

from __future__ import annotations

import shutil
import sys

from runectl.trace.events import (
    BudgetExhausted,
    ChallengeLoaded,
    CostUpdated,
    ErrorEvent,
    Event,
    EventPayload,
    FlagCandidate,
    FlagDecision,
    FlagReviewed,
    LlmResponse,
    RunFinished,
    RunStarted,
    StrategyShift,
    ToolCall,
    ToolResultEvent,
    TriageResult,
)


def render_ndjson(event: Event) -> None:
    print(event.model_dump_json(), file=sys.stdout, flush=True)


def _width() -> int:
    # Fall back to a sane fixed width when stderr is redirected to a file.
    return max(60, min(shutil.get_terminal_size((100, 24)).columns, 140))


def _flat(text: str, limit: int) -> str:
    """Collapse to one line and clip, so a 3KB exploit script stays one row."""
    single = " ".join(text.split())
    if len(single) <= limit:
        return single
    return single[: limit - 1] + "…"


def _tool_arg_summary(tool: str, arguments: dict[str, object], limit: int) -> str:
    """Show the argument that actually says what the step is doing."""
    for key in ("command", "flag_pattern", "filename", "binary_path", "flag"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return _flat(value, limit)
    return _flat(str(arguments), limit)


def _line(payload: EventPayload, width: int) -> str | None:
    """One display line for an event, or None for events not worth showing live."""
    budget = width - 22

    if isinstance(payload, RunStarted):
        return (
            f"  ▶ {payload.challenge_name} [{payload.category}]  {payload.model}\n"
            f"    max {payload.max_steps} steps · network={payload.network} · "
            f"approval={payload.approval_policy}"
        )
    if isinstance(payload, ChallengeLoaded):
        files = f"{payload.file_count} file(s)" if payload.file_count else "no files"
        return f"    {files} · {payload.description_chars} chars of description"
    if isinstance(payload, TriageResult):
        return f"    triage: {len(payload.commands)} command(s)"
    if isinstance(payload, ToolCall):
        summary = _tool_arg_summary(payload.tool, payload.arguments, budget)
        return f"{payload.step:>3} → {payload.tool:<12} {summary}"
    if isinstance(payload, ToolResultEvent):
        mark = "ok " if payload.ok else ("blocked" if payload.kind == "blocked" else "err")
        body = payload.stdout or payload.stderr
        return f"{payload.step:>3} ← {mark:<12} {_flat(body, budget - 8)} ({payload.duration_s:.1f}s)"
    if isinstance(payload, LlmResponse):
        if payload.tool_call is None and payload.text_chars:
            return f"{payload.step:>3} · model replied with text and no tool call"
        return None  # the tool.call line that follows says more
    if isinstance(payload, CostUpdated):
        return f"{payload.step:>3} $ {payload.cost_usd:.4f}  (run total ${payload.cumulative_cost_usd:.4f})"
    if isinstance(payload, StrategyShift):
        return f"{payload.step:>3} ⟳ strategy shift: {_flat(payload.evidence_summary, budget)}"
    if isinstance(payload, BudgetExhausted):
        return (
            f"{payload.step:>3} ■ spend ceiling reached: ${payload.spent_usd:.4f} "
            f"of ${payload.limit_usd:.2f} — stopping"
        )
    if isinstance(payload, FlagCandidate):
        return f"{payload.step:>3} ? candidate {payload.flag}"
    if isinstance(payload, FlagReviewed):
        # The reviewer's own words, not a summary of them — reading why a model
        # doubted a flag is the entire point of the pass.
        mark = "ok" if payload.sound else "doubts"
        return f"{payload.step:>3} ⚖ review {mark}: {_flat(payload.reason, width - 20)}"
    if isinstance(payload, FlagDecision):
        mark = {"finalized": "✓", "pending": "…", "rejected": "✗"}.get(payload.decision, "?")
        reason = _flat(payload.reason, width - 8)
        return f"{payload.step:>3} {mark} {payload.decision}: {payload.flag}\n      {reason}"
    if isinstance(payload, ErrorEvent):
        step = f"{payload.step:>3}" if payload.step is not None else "  ·"
        return f"{step} ! {payload.kind}: {_flat(payload.message, budget)}"
    if isinstance(payload, RunFinished):
        ratio = (payload.progress_steps / payload.steps_used * 100) if payload.steps_used else 0.0
        head = f"  ■ {payload.outcome}"
        if payload.flag:
            head += f" — {payload.flag}"
        return (
            f"{head}\n"
            f"    {payload.steps_used} steps ({payload.progress_steps} with progress, "
            f"{ratio:.0f}%) · {payload.blocked_steps} blocked\n"
            f"    ${payload.cost_usd:.4f} · {payload.duration_s:.0f}s · exit {payload.exit_code}"
        )
    return None


def render_human(event: Event) -> None:
    line = _line(event.payload(), _width())
    if line is not None:
        print(line, file=sys.stderr, flush=True)
