"""Context management: summarize, don't truncate; one limit (D12, plan §5.4).

NOTE on scope: token-budget compaction is approximated here by a message-count
threshold, not a real per-provider token count — accurate tokenization is a
later refinement, not needed to prove the mechanism for the M4 skeleton. LLM
summarization (both for oversized tool output and for history compaction) is
wired as an injected callback so this module stays provider-agnostic and
testable without a model; the runner supplies a callback that goes through the
standard `providers.base.complete_with_retry` path so every summarization call
is costed like any other (D5, D12).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from runectl.categories.schema import Category
from runectl.config import DEFAULT_MAX_HISTORY_MESSAGES, DEFAULT_TOOL_OUTPUT_LIMIT
from runectl.providers.base import Message

_SALIENT_PATTERN = re.compile(r"(?i)flag\{|error|fail|not found|permission denied|[A-Za-z0-9+/]{20,}={0,2}")

BASE_RULES = """\
You are a CTF solving agent working inside a sandbox at /ctf/.
Use exactly one tool call per turn. Never guess a flag: only submit_flag a value
you observed verbatim in a tool's output, and say how you found it. Do not ask
for approval; act on the best next step yourself. If two actions in a row on the
same hypothesis produce no new information, change approach rather than
repeating a wider version of the same command. Decode any base64/hex/encoded
string fully the moment you see it, before moving on to something else. No flag
with solid evidence beats a guess: if you cannot find one, say so plainly rather
than submitting something unsupported.
"""


def build_system_prompt(category: Category) -> str:
    """The cacheable static prefix (D5 prompt caching lands on this string unchanged
    across steps; D12's "one static prefix" is this function, not a per-step rebuild)."""
    return f"{BASE_RULES}\nCATEGORY: {category.name}\n{category.brief}\n\n{category.playbook}"


def summarize_tool_output(
    text: str,
    *,
    limit: int = DEFAULT_TOOL_OUTPUT_LIMIT,
    llm_summarize: Callable[[str], str] | None = None,
) -> str:
    """Extractive summarization (head+tail+salient) first; LLM only if still over."""
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    salient = [line for line in lines if _SALIENT_PATTERN.search(line)]
    extracted = (
        f"[full output is {len(text)} chars; truncated here, complete copy is in the trace]\n"
        f"--- head ---\n{chr(10).join(lines[:20])}\n"
        f"--- salient ---\n{chr(10).join(salient[:20])}\n"
        f"--- tail ---\n{chr(10).join(lines[-20:])}\n"
    )
    if len(extracted) <= limit:
        return extracted
    if llm_summarize is not None:
        return llm_summarize(extracted)[:limit]
    return extracted[:limit]


@dataclass
class ContextBuilder:
    """Dedupes identical tool output by digest (plan §4.5/§5.4), applies the one
    context limit (D12), and owns history compaction."""

    limit: int = DEFAULT_TOOL_OUTPUT_LIMIT
    max_messages: int = DEFAULT_MAX_HISTORY_MESSAGES
    llm_summarize: Callable[[str], str] | None = None
    _seen_digests: dict[str, int] = field(default_factory=dict)

    def render_tool_output(self, text: str, *, step: int) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        seen_at = self._seen_digests.get(digest)
        if seen_at is not None:
            return f"[identical to output already seen at step {seen_at}]"
        self._seen_digests[digest] = step
        return summarize_tool_output(text, limit=self.limit, llm_summarize=self.llm_summarize)

    def maybe_compact(self, history: list[Message]) -> list[Message]:
        """Compact once history outgrows the budget; a no-op below it."""
        return compact_history(
            history, max_messages=self.max_messages, summarize=self._summarize_messages
        )

    def _summarize_messages(self, messages: list[Message]) -> str:
        rendered = "\n".join(f"[{m.role}] {m.content}" for m in messages)
        if self.llm_summarize is not None:
            return self.llm_summarize(rendered)
        # No utility model wired (tests, replay): fall back to the deterministic
        # extractive path rather than dropping the older context silently.
        return summarize_tool_output(rendered, limit=self.limit)


def compact_history(
    history: list[Message],
    *,
    max_messages: int,
    summarize: Callable[[list[Message]], str],
) -> list[Message]:
    """Once history exceeds ``max_messages``, replace the older portion with one
    summary message produced via the standard provider path (D5, D12).

    The compacted result is at most ``max_messages`` long *including* the summary
    message, so a compacted history is never immediately over budget again — that
    would re-summarize the summary on the very next step.

    The split point is never allowed to land between an assistant's tool call and
    its tool results: a retained ``tool`` message whose originating assistant turn
    was compacted away is an orphan, and every provider rejects those. The
    boundary walks forward past any leading tool messages, so the retained tail
    always starts on a clean turn.
    """
    if len(history) <= max_messages:
        return history
    split = len(history) - max(max_messages - 1, 1)
    while split < len(history) and history[split].role == "tool":
        split += 1
    if split >= len(history):
        return history
    older, recent = history[:split], history[split:]
    summary_text = summarize(older)
    summary_message = Message(
        role="user", content=f"[compacted summary of {len(older)} earlier messages]\n{summary_text}"
    )
    return [summary_message, *recent]
