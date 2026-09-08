"""The walking-skeleton loop: decide -> one tool call -> observe (D6, plan §5.2).

Category-agnostic; every branch emits a trace event and the runner never
prints (D4/D13 — only `cli/` renders). Progress scoring/budgets (M5) and the
full false-flag subsystem (M6) are no-op-shaped stubs here — see
`loop/state.py` and `flags/judge.py` for the exact seams they replace without
reshaping this loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from runectl.categories.schema import Category
from runectl.config import DEFAULT_MAX_COST_USD
from runectl.errors import ProviderError, SandboxError
from runectl.flags.judge import ToolObservation, judge_candidate
from runectl.loop import nudges
from runectl.loop.context import ContextBuilder, build_system_prompt
from runectl.loop.state import Challenge, RunState
from runectl.loop.triage import triage as run_triage
from runectl.providers.base import Message, Provider, ToolCallRequest, complete_with_retry
from runectl.providers.cost import CostLedger
from runectl.providers.registry import ModelInfo
from runectl.sandbox.base import Sandbox
from runectl.tools.dispatch import ToolDispatcher
from runectl.tools.schema import TOOLS
from runectl.trace.events import (
    BudgetExhausted,
    ChallengeLoaded,
    CostUpdated,
    ErrorEvent,
    FlagCandidate,
    FlagDecision,
    LlmRequest,
    LlmResponse,
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

Outcome = Literal["solved", "candidate", "exhausted", "error"]


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
    ) -> None:
        self._provider = provider
        self._sandbox = sandbox
        self._writer = writer
        self._max_tokens = max_tokens
        # D19 — 0 disables the ceiling; anything else stops the run cleanly the
        # moment cumulative spend crosses it.
        self._max_cost_usd = max_cost_usd
        self._triage_override = triage_override
        self._dispatcher = ToolDispatcher(sandbox)
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
        consecutive_no_progress = 0

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
                Message(role="assistant", content=completion.text, tool_calls=completion.tool_calls)
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
                finalized = self._handle_submit_flag(step, primary, state)
                if finalized:
                    outcome, exit_code = "solved", 0
                    break
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
            state.tool_observations.append(
                ToolObservation(seq=self._writer.seq, stdout=result.stdout, stderr=result.stderr)
            )
            rendered = self._context.render_tool_output(result.stdout or result.stderr, step=step)
            state.history.append(
                Message(role="tool", tool_call_id=primary.id, tool_name=primary.name, content=rendered)
            )

            # M5 stub: real fingerprint/signal scoring isn't built yet. This crude
            # proxy (ok output = progress, error/blocked = not) exists so run.json's
            # progress ratio (D16) and the stuck-nudge have something real to use.
            if result.kind == "output":
                state.progress_steps += 1
                consecutive_no_progress = 0
            else:
                if result.kind == "blocked":
                    state.blocked_steps += 1
                consecutive_no_progress += 1

            nudge_text = nudges.stuck_new_hypothesis(state, consecutive_no_progress=consecutive_no_progress)
            if nudge_text:
                self._writer.emit(
                    StrategyShift(step=step, reason="no-progress threshold", evidence_summary=nudge_text)
                )
                state.history.append(Message(role="user", content=nudge_text))
                consecutive_no_progress = 0

        return self._finish(started, outcome=outcome, exit_code=exit_code)

    def _handle_submit_flag(self, step: int, call: ToolCallRequest, state: RunState) -> bool:
        flag = str(call.arguments.get("flag", ""))
        how_found = str(call.arguments.get("how_found", ""))
        fallback_seq = self._writer.seq + 1
        decision = judge_candidate(flag=flag, history=state.tool_observations, fallback_seq=fallback_seq)
        self._writer.emit(
            FlagCandidate(step=step, flag=flag, how_found=how_found, provenance_seq=decision.provenance_seq)
        )
        if decision.accepted:
            self._writer.emit(
                FlagDecision(step=step, flag=flag, decision="finalized", reason=decision.reason)
            )
            state.flag = flag
            return True
        self._writer.emit(FlagDecision(step=step, flag=flag, decision="rejected", reason=decision.reason))
        state.history.append(
            Message(
                role="tool", tool_call_id=call.id, tool_name=call.name,
                content=f"rejected: {decision.reason}",
            )
        )
        return False

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
        )
