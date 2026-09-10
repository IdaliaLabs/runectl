"""The loop: decide -> one tool call -> observe (D6, plan §5.2).

Category-agnostic; every branch emits a trace event and the runner never
prints (D4/D13 — only `cli/` renders). Two collaborators do the judging and
never mutate state themselves: `progress.tracker` scores each step and blocks
dead ideas (D8/D16), and `flags.judge` decides what a submitted flag is
(D15/D11). The loop applies what they return, which is the whole reason a
`submit_flag` has three outcomes here — solved, held for approval, or rejected
back to the model as feedback.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Literal

from runectl.categories.schema import Category
from runectl.config import DEFAULT_MAX_COST_USD
from runectl.errors import ProviderError, SandboxError
from runectl.flags.judge import FlagJudge, ToolObservation
from runectl.flags.review import Reviewer
from runectl.loop import nudges
from runectl.loop.context import ContextBuilder, build_system_prompt
from runectl.loop.state import Challenge, RunState
from runectl.loop.triage import triage as run_triage
from runectl.progress.tracker import ProgressTracker
from runectl.providers.base import Message, Provider, ToolCallRequest, complete_with_retry
from runectl.providers.cost import CostLedger
from runectl.providers.registry import ModelInfo, ThinkingLevel, resolve_thinking_level
from runectl.sandbox.base import Sandbox
from runectl.tools.dispatch import ToolDispatcher
from runectl.tools.schema import TOOLS
from runectl.trace.events import (
    BudgetBlocked,
    BudgetExhausted,
    ChallengeLoaded,
    CostUpdated,
    ErrorEvent,
    FlagCandidate,
    FlagDecision,
    FlagReviewed,
    LlmRequest,
    LlmResponse,
    LlmThinking,
    ProgressScored,
    RunFinished,
    RunStarted,
    StrategyShift,
    ToolCall,
    ToolCallSummary,
    ToolResultEvent,
    TriageResult,
)
from runectl.trace.writer import TraceWriter

DEFAULT_MAX_TOKENS = 4096

# D20 — a display cap, independent of the writer's generic >8KB artifact spill
# (trace/writer.py). That mechanism spills any oversized *string* to an
# artifact file and replaces it in the JSON with an `{"$artifact": ...}` dict —
# which is exactly what's already written for `tool.result.stdout`/`stderr`
# today, both of which are declared as plain `str` fields on their payload
# class. Re-reading such an event through `Event.payload()` (which
# `model_validate`s the raw dict back against that `str`-typed field) would
# raise a pydantic validation error, not silently succeed. `LlmThinking.text`
# is capped here instead of relying on that path, so a long `xhigh`/`max`
# thinking block can never produce an unreadable trace line.
_MAX_THINKING_CHARS = 8_000


def _command_text(arguments: dict[str, object]) -> str:
    """The part of a tool call that carries the *idea*, for classification.

    `run_command` has `command`; `search_flag` has a pattern; `run_gdb` has a
    binary plus a script. Falling back to the whole argument blob keeps every
    tool classifiable rather than silently landing in `other`.
    """
    for key in ("command", "flag_pattern", "binary_path", "filename"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            extra = arguments.get("gdb_commands")
            if key == "binary_path" and isinstance(extra, list):
                return f"gdb {value} " + " ".join(str(c) for c in extra)
            return value
    return " ".join(str(v) for v in arguments.values())

Outcome = Literal["solved", "candidate", "exhausted", "error"]
Decision = Literal["finalized", "pending", "rejected"]


@dataclass(frozen=True)
class RunOutcome:
    outcome: Outcome
    flag: str | None
    exit_code: int
    steps_used: int
    cost_usd: float
    # D16: the progress ratio is the primary metric, so it has to reach run.json,
    # not only the run.finished event.
    progress_steps: int = 0
    blocked_steps: int = 0
    # D20 — the *resolved* level (post-clamp), same rule as progress_steps
    # above: it has to reach run.json, not only the run.started event, so a
    # run's reasoning spend is discoverable without re-reading trace.jsonl.
    thinking_level: str = "off"


class Runner:
    def __init__(
        self,
        *,
        challenge: Challenge,
        category: Category,
        model: ModelInfo,
        provider: Provider,
        sandbox: Sandbox,
        writer: TraceWriter,
        approval_policy: str = "gated",
        max_cost_usd: float = DEFAULT_MAX_COST_USD,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        triage_override: TriageResult | None = None,
        ledger: CostLedger | None = None,
        context: ContextBuilder | None = None,
        reviewer: Reviewer | None = None,
        thinking: ThinkingLevel = "off",
    ) -> None:
        self._provider = provider
        self._sandbox = sandbox
        self._writer = writer
        self._max_tokens = max_tokens
        # D20 — resolved once, here, so both `runectl run` and `runectl bench
        # run` (which both build a `Runner` through the shared `execute_run`
        # seam) get the same clamp behavior for free, with no CLI-layer
        # duplication. `_thinking_requested` is kept only to report a clamp.
        self._thinking_requested = thinking
        self._thinking_level, self._thinking_clamped = resolve_thinking_level(model, thinking)
        # D19 — 0 disables the ceiling; anything else stops the run cleanly the
        # moment cumulative spend crosses it.
        self._max_cost_usd = max_cost_usd
        self._triage_override = triage_override
        self._dispatcher = ToolDispatcher(sandbox)
        # D15: the false-flag subsystem. It gets the sandbox (to re-derive a
        # cited command), the description (to spot a lure the author pasted in)
        # and the flag format — and returns verdicts the loop applies (D6).
        self._judge = FlagJudge(
            approval_policy=approval_policy,
            flag_format=challenge.flag_format,
            description=challenge.description,
            sandbox=sandbox,
            reviewer=reviewer,
        )
        # D8: the four progress mechanisms. A plain collaborator (D6) — the loop
        # applies what it returns; it never touches RunState itself.
        self._progress = ProgressTracker(category=category)
        # A caller that wants utility/summarization calls costed alongside the
        # main loop's (D5, D12) passes a ledger/context pre-wired to a
        # utility-model summarizer (providers.base.make_utility_summarizer);
        # otherwise each run gets its own plain ones.
        self._ledger = ledger if ledger is not None else CostLedger()
        self._context = context if context is not None else ContextBuilder()
        self.state = RunState(
            challenge=challenge,
            category=category,
            model=model,
            provider_name=model.provider,
            approval_policy=approval_policy,
            max_steps=category.step_limit,
        )

    def run(self) -> RunOutcome:
        state = self.state
        started = time.monotonic()

        self._writer.emit(
            RunStarted(
                challenge_name=state.challenge.name,
                category=state.category.name,
                model=state.model.id,
                provider=state.provider_name,
                approval_policy=state.approval_policy,
                max_steps=state.max_steps,
                network=state.category.network,
                thinking_level=self._thinking_level,
                thinking_clamped_from=(
                    self._thinking_requested if self._thinking_clamped else None
                ),
            )
        )
        self._writer.emit(
            ChallengeLoaded(
                name=state.challenge.name,
                category=state.challenge.category,
                description_chars=len(state.challenge.description),
                file_count=len(state.challenge.files),
                flag_format=state.challenge.flag_format,
            )
        )

        if state.challenge.files:
            try:
                self._sandbox.put_inputs(list(state.challenge.files))
            except SandboxError as exc:
                self._writer.emit(ErrorEvent(step=None, kind="sandbox", message=str(exc), recoverable=False))
                return self._finish(started, outcome="error", exit_code=4)

        # Replay seam: triage's raw exec() calls aren't individually recorded in
        # the trace (only the aggregated triage.result event is), so a replay
        # run supplies that recorded result instead of re-running triage() live
        # against ReplaySandbox — see sandbox/replay.py's docstring.
        triage_result = self._triage_override or run_triage(self._sandbox, state.category)
        self._writer.emit(triage_result)

        system_prompt = build_system_prompt(state.category)
        initial_lines = "\n".join(f"$ {cmd}\n{output}" for cmd, output in triage_result.findings.items())
        initial_content = (
            f"CHALLENGE: {state.challenge.name}\n"
            f"CATEGORY: {state.challenge.category}\n"
            f"DESCRIPTION:\n{state.challenge.description}\n\n"
            f"TRIAGE FINDINGS:\n{initial_lines}"
        )
        state.history.append(Message(role="user", content=initial_content))

        outcome: Outcome = "exhausted"
        exit_code = 3

        for step in range(1, state.max_steps + 1):
            state.step = step
            # D12: compaction is a context-pressure trigger, not a step counter —
            # it runs before every request and no-ops until history is actually long.
            state.history = self._context.maybe_compact(state.history)
            self._writer.emit(
                LlmRequest(
                    step=step,
                    model=state.model.id,
                    provider=state.provider_name,
                    message_count=len(state.history),
                    cached_prefix=state.model.supports_prompt_cache,
                )
            )
            try:
                completion = complete_with_retry(
                    self._provider,
                    state.model,
                    self._ledger,
                    system=system_prompt,
                    messages=state.history,
                    tools=TOOLS,
                    max_tokens=self._max_tokens,
                    thinking=self._thinking_level,
                )
            except ProviderError as exc:
                self._writer.emit(ErrorEvent(step=step, kind="provider", message=str(exc), recoverable=False))
                outcome, exit_code = "error", 5
                break

            state.cost_usd = self._ledger.total_usd
            last_cost = self._ledger.entries[-1].cost_usd if self._ledger.entries else 0.0
            self._writer.emit(
                CostUpdated(
                    step=step,
                    provider=state.provider_name,
                    model=state.model.id,
                    input_tokens=completion.usage.input_tokens,
                    output_tokens=completion.usage.output_tokens,
                    cost_usd=last_cost,
                    cumulative_cost_usd=state.cost_usd,
                )
            )

            # D19: check immediately after the ledger updates, so the run stops
            # before paying for another call rather than one call too late.
            if self._max_cost_usd > 0 and state.cost_usd >= self._max_cost_usd:
                self._writer.emit(
                    BudgetExhausted(
                        step=step, limit_usd=self._max_cost_usd, spent_usd=state.cost_usd
                    )
                )
                outcome, exit_code = "exhausted", 3
                break

            # D20 — emitted before llm.response so a live or replayed timeline
            # shows the reasoning ahead of what it produced. Nothing is emitted
            # when thinking wasn't requested or a provider returned no text for
            # it (e.g. OpenAI's Chat Completions surface never does — see
            # providers/openai.py) — an empty event would just be noise.
            if completion.thinking_text:
                thinking_text = completion.thinking_text
                truncated = len(thinking_text) > _MAX_THINKING_CHARS
                if truncated:
                    thinking_text = thinking_text[:_MAX_THINKING_CHARS]
                self._writer.emit(
                    LlmThinking(
                        step=step, text=thinking_text, level=self._thinking_level, truncated=truncated
                    )
                )

            tool_call_summary = None
            if completion.tool_calls:
                first = completion.tool_calls[0]
                tool_call_summary = ToolCallSummary(name=first.name, arguments=first.arguments)
            self._writer.emit(
                LlmResponse(
                    step=step,
                    model=state.model.id,
                    provider=state.provider_name,
                    stop_reason=completion.stop_reason,
                    text_chars=len(completion.text),
                    tool_call=tool_call_summary,
                    input_tokens=completion.usage.input_tokens,
                    output_tokens=completion.usage.output_tokens,
                    cost_usd=last_cost,
                )
            )
            state.history.append(
                Message(
                    role="assistant",
                    content=completion.text,
                    tool_calls=completion.tool_calls,
                    thinking_blocks=completion.thinking_blocks,
                )
            )

            if not completion.tool_calls:
                nudge = (
                    nudges.act_dont_ask()
                    if nudges.looks_like_approval_seeking(completion.text)
                    else nudges.no_tool_call()
                )
                state.history.append(Message(role="user", content=nudge))
                continue

            # D7: exactly one tool call is executed per step; extras are rejected
            # (blocked) but still need a matching tool-result message so the next
            # request stays valid for providers that require one per tool_use.
            primary, *extras = completion.tool_calls
            for extra in extras:
                self._writer.emit(
                    ToolResultEvent(
                        step=step, tool=extra.name, ok=False, kind="blocked",
                        stdout="", stderr="rejected: only one tool call is executed per step",
                        duration_s=0.0, truncated=False,
                    )
                )
                state.blocked_steps += 1
                state.history.append(
                    Message(
                        role="tool", tool_call_id=extra.id, tool_name=extra.name,
                        content="blocked: only one tool call is executed per step",
                    )
                )

            if primary.name == "submit_flag":
                decision = self._handle_submit_flag(step, primary, state)
                if decision == "finalized":
                    outcome, exit_code = "solved", 0
                    break
                if decision == "pending":
                    # D11: a candidate that cleared the unconditional checks but
                    # not the auto-finalize bar stops the run and exits 2, with
                    # the candidate in the trace for `runectl flag approve`.
                    outcome, exit_code = "candidate", 2
                    break
                continue

            # D8 mechanism 4: budgets block *before* execution. A blocked call
            # costs no sandbox time and no further tokens on a dead idea.
            command_text = _command_text(primary.arguments)
            verdict = self._progress.check_budget(command_text)
            if verdict.blocked:
                self._writer.emit(
                    BudgetBlocked(step=step, family=verdict.family, reason=verdict.reason)
                )
                state.blocked_steps += 1
                # A blocked step is still a wasted step: it has to count toward
                # the no-progress run or the forced shift can never fire once
                # budgets start biting.
                self._progress.note_blocked(verdict.family, verdict.reason, step=step)
                state.history.append(
                    Message(
                        role="tool", tool_call_id=primary.id, tool_name=primary.name,
                        content=f"blocked: {verdict.reason}",
                    )
                )
                shift = self._progress.due_shift()
                if shift:
                    self._writer.emit(
                        StrategyShift(step=step, reason="budget", evidence_summary=shift)
                    )
                    state.history.append(Message(role="user", content=shift))
                continue

            self._writer.emit(ToolCall(step=step, tool=primary.name, arguments=primary.arguments))
            result = self._dispatcher.dispatch(primary.name, primary.arguments)
            self._writer.emit(
                ToolResultEvent(
                    step=step, tool=primary.name, ok=result.ok, kind=result.kind,
                    stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code,
                    duration_s=result.duration_s, truncated=result.truncated,
                )
            )
            observation_seq = self._writer.seq
            state.tool_observations.append(
                ToolObservation(
                    seq=observation_seq,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    # Kept so the judge can tell a discovery from the agent
                    # echoing its own guess back (D15 §1).
                    command=json.dumps(primary.arguments, sort_keys=True, default=str),
                    tool=primary.name,
                    shell_command=result.shell_command,
                )
            )
            rendered = self._context.render_tool_output(result.stdout or result.stderr, step=step)
            # D15 §1 requires the agent to cite where it saw a flag. It can only
            # do that if it can see the seq numbers, so every tool result carries
            # its own — this header is the provenance vocabulary.
            state.history.append(
                Message(
                    role="tool",
                    tool_call_id=primary.id,
                    tool_name=primary.name,
                    content=f"[observation seq={observation_seq}]\n{rendered}",
                )
            )

            # D8 mechanisms 1-3: classify, fingerprint, score. "Progress" means
            # new information, not a zero exit code (D16).
            assessment = self._progress.record(
                command_text, result.stdout or result.stderr, ok=result.ok, step=step
            )
            self._writer.emit(
                ProgressScored(
                    step=step,
                    family=assessment.family,
                    fingerprint=assessment.fingerprint,
                    delta=assessment.delta,
                    signal=assessment.signal,
                )
            )
            if assessment.progressed:
                state.progress_steps += 1
            if result.kind == "blocked":
                state.blocked_steps += 1

            shift_text = self._progress.due_shift()
            if shift_text:
                self._writer.emit(
                    StrategyShift(
                        step=step,
                        reason=self._progress.shift_reason_kind,
                        evidence_summary=shift_text,
                    )
                )
                state.history.append(Message(role="user", content=shift_text))

        return self._finish(started, outcome=outcome, exit_code=exit_code)

    def _handle_submit_flag(self, step: int, call: ToolCallRequest, state: RunState) -> Decision:
        """Run the D15 pipeline over one candidate and apply what it returns."""
        flag = str(call.arguments.get("flag", ""))
        how_found = str(call.arguments.get("how_found", ""))
        provenance = str(call.arguments.get("provenance", ""))
        fallback_seq = self._writer.seq + 1
        verdict = self._judge.judge(
            flag=flag,
            history=state.tool_observations,
            fallback_seq=fallback_seq,
            provenance=provenance,
            how_found=how_found,
        )
        self._writer.emit(
            FlagCandidate(step=step, flag=flag, how_found=how_found, provenance_seq=verdict.provenance_seq)
        )
        for check in verdict.checks:
            if check.name == "review":
                self._writer.emit(
                    FlagReviewed(step=step, flag=flag, sound=check.passed, reason=check.detail)
                )
        self._writer.emit(
            FlagDecision(step=step, flag=flag, decision=verdict.decision, reason=verdict.reason)
        )
        if verdict.decision == "rejected":
            # Feedback, not failure: the run continues and the agent gets to
            # act on why (D15 §5 — no flag beats a wrong one).
            state.history.append(
                Message(
                    role="tool", tool_call_id=call.id, tool_name=call.name,
                    content=f"rejected: {verdict.reason}",
                )
            )
            return "rejected"
        # Both `finalized` and `pending` record the flag: a pending candidate is
        # the run's answer, it just is not one the tool will claim unattended.
        state.flag = flag
        return verdict.decision

    def _finish(self, started: float, *, outcome: Outcome, exit_code: int) -> RunOutcome:
        state = self.state
        duration = time.monotonic() - started
        self._writer.emit(
            RunFinished(
                outcome=outcome,
                flag=state.flag,
                steps_used=state.step,
                progress_steps=state.progress_steps,
                blocked_steps=state.blocked_steps,
                cost_usd=state.cost_usd,
                duration_s=duration,
                exit_code=exit_code,
            )
        )
        return RunOutcome(
            outcome=outcome, flag=state.flag, exit_code=exit_code,
            steps_used=state.step, cost_usd=state.cost_usd,
            progress_steps=state.progress_steps, blocked_steps=state.blocked_steps,
            thinking_level=self._thinking_level,
        )
