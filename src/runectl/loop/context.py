"""Context management: summarize, don't truncate; one limit (D12).

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
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from runectl.categories.schema import Category
from runectl.config import DEFAULT_MAX_HISTORY_MESSAGES, DEFAULT_TOOL_OUTPUT_LIMIT
from runectl.providers.base import Message

_SALIENT_PATTERN = re.compile(
    r"(?i)[A-Za-z0-9_]{2,}\{[^}]{4,}\}|flag\{|error|fail|not found|permission denied|"
    r"[A-Za-z0-9+/]{20,}={0,2}"
)

BASE_RULES = """\
You are a CTF solving agent working inside a sandbox at /ctf/.
Use exactly one tool call per turn. Never guess a flag: only submit_flag a value
you observed verbatim in a tool's output, and say how you found it. If the flag
is a value you compute inside a wrapper the challenge states, printing the
computed value alone is enough — never echo the assembled flag back through a
command. Do not ask
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
    # Bound each line: a single multi-megabyte line (a hex dump, base64 of a
    # binary) would otherwise make the "head"/"tail" slices enormous and be sent
    # verbatim to the summarizer model.
    lines = [line[:2000] for line in text.splitlines()]
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
        # Cap what reaches the model regardless of how large `extracted` grew.
        return llm_summarize(extracted[: limit * 4])[:limit]
    return extracted[:limit]


@dataclass
class ContextBuilder:
    """Dedupes identical tool output by digest, applies the one
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
        rendered = "\n".join(_render_for_summary(m) for m in messages)
        if self.llm_summarize is not None:
            summary = self.llm_summarize(rendered)
            if summary.strip():
                return summary
            # The utility model returned nothing (e.g. its own reasoning consumed
            # the token budget). Fall through to the deterministic extractive path
            # rather than dropping the older context (D12 promises never to drop).
        # No utility model wired (tests, replay), or an empty summary above.
        return summarize_tool_output(rendered, limit=self.limit)


def _render_for_summary(message: Message) -> str:
    """One message rendered for the summarizer, including any tool call it made.

    An assistant turn that called a tool usually has empty ``content``; without
    the call's arguments the summarizer never learns which commands were tried,
    which is exactly what the next steps need to not repeat them.
    """
    parts = [f"[{message.role}] {message.content}".rstrip()]
    for call in message.tool_calls:
        parts.append(
            f"[{message.role} ran {call.name}] "
            + json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
        )
    return "\n".join(parts)


def compact_history(
    history: list[Message],
    *,
    max_messages: int,
    summarize: Callable[[list[Message]], str],
) -> list[Message]:
    """Simple compaction (D12, amended 2026-09-16): once history exceeds
    ``max_messages``, fold everything after the pinned first message into a single
    summary and replay nothing verbatim before it.

    ``history[0]`` is the challenge message (name, category, description, flag
    format, triage findings); it is pinned so those facts stay verbatim for the
    whole run instead of decaying through a summary of a summary.

    This is Anthropic's documented compaction shape, and it replaces an earlier
    keep-tail scheme that trimmed to exactly the budget and so re-fired on every
    step once tripped. Two properties matter: the result is two messages, far
    under the budget, so it does not thrash; and no verbatim assistant turn is
    replayed across the boundary, so a provider-native thinking block is never
    sent against an edited prefix — the shape the preserved-thinking
    history-editing check rejects.
    """
    if len(history) <= max_messages or len(history) <= 1:
        return history
    head, older = history[0], history[1:]
    summary_text = summarize(older)
    summary_message = Message(
        role="user",
        content=f"[compacted summary of {len(older)} earlier messages]\n{summary_text}",
    )
    return [head, summary_message]
