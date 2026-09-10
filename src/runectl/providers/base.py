"""Provider protocol and the provider-agnostic retry/degrade path (D5, plan §3.1, 3.4).

Non-streaming request/response in the core (D5): this is what makes retries,
cassettes, and replay determinism straightforward. Every LLM call — the main
loop and internal utility calls (summarization, retry summaries) alike — goes
through :func:`complete_with_retry`, so nothing silently no-ops and every
token is costed (`REBUILD_NOTES.md` §3, "silent provider no-ops").
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from runectl.config import DEFAULT_RETRY_ATTEMPTS
from runectl.errors import ProviderError, UsageError
from runectl.providers.cost import CostLedger
from runectl.providers.registry import ModelInfo, ThinkingLevel
from runectl.tools.schema import ToolSchema


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]


class Usage(BaseModel):
    """Token counts for one call.

    ``input_tokens`` is *uncached* input only — cached tokens are reported
    separately by the provider and billed at different rates (D18), so folding
    them together would silently overstate cost by up to 10x on a cache hit.
    Providers without prompt caching leave the cache fields at zero.
    """

    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


class Message(BaseModel):
    """One canonical chat message; each adapter derives its own wire shape."""

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_call_id: str | None = None  # set when role == "tool"
    # set when role == "tool" — Google's FunctionResponse needs the name, not just an id
    tool_name: str | None = None
    tool_calls: tuple[ToolCallRequest, ...] = ()  # set when role == "assistant" and it called a tool
    # D20 — opaque provider-native thinking blocks from a prior assistant turn.
    # Replayed back verbatim, in the position each provider's own API requires
    # (e.g. Anthropic wants them first in the assistant content list, before
    # text and tool_use). Never inspected or modified by core code — only the
    # adapter that produced them knows their shape.
    thinking_blocks: tuple[dict[str, Any], ...] = ()


class Completion(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    tool_calls: tuple[ToolCallRequest, ...]
    usage: Usage
    stop_reason: str
    # D20 — the human-readable thinking summary (empty if thinking was off, or
    # if a provider's display setting withheld it) and the opaque blocks to
    # replay back on the next turn (see Message.thinking_blocks).
    thinking_text: str = ""
    thinking_blocks: tuple[dict[str, Any], ...] = ()


class TransientProviderError(Exception):
    """Raised by an adapter for a 429/5xx/timeout. Retried with backoff; never
    escapes :func:`complete_with_retry` — callers only ever see :class:`ProviderError`."""


def auth_error(provider: str, exc: Exception) -> UsageError:
    """A rejected/invalid API key is a config error, not a provider outage.

    Adapters raise this (never a raw SDK ``AuthenticationError``) so a bad key
    exits cleanly with code 6 and an actionable message instead of a traceback.
    It is *not* a :class:`TransientProviderError`, so it is never retried —
    hammering a bad key four times only wastes the user's time.
    """
    return UsageError(
        f"{provider} rejected the API credentials ({exc}). Check the key with "
        f"`runectl keys set {provider.lower()}`, the {provider.upper()}_API_KEY "
        f"environment variable, or pass --api-key."
    )


def api_error(provider: str, exc: Exception) -> ProviderError:
    """A non-transient, non-auth API failure (a 400/404/422, an unexpected 4xx).

    Surfaced as a clean :class:`ProviderError` (exit 5) rather than letting the
    raw SDK exception escape as a traceback. Not retried — these do not clear on
    their own the way a 429/5xx does."""
    return ProviderError(f"{provider} API call failed: {exc}")


class Provider(Protocol):
    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
        thinking: ThinkingLevel = "off",
    ) -> Completion: ...


def complete_with_retry(
    provider: Provider,
    model: ModelInfo,
    ledger: CostLedger,
    *,
    system: str,
    messages: Sequence[Message],
    tools: Sequence[ToolSchema],
    max_tokens: int,
    thinking: ThinkingLevel = "off",
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> Completion:
    last_error: TransientProviderError | None = None
    for attempt in range(attempts):
        try:
            completion = provider.complete(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                thinking=thinking,
            )
        except TransientProviderError as exc:
            last_error = exc
            if attempt < attempts - 1:
                sleep((2**attempt) + random.uniform(0, 1))
            continue
        ledger.record(
            model,
            input_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
            cache_read_tokens=completion.usage.cache_read_tokens,
            cache_write_tokens=completion.usage.cache_write_tokens,
        )
        return completion
    raise ProviderError(f"{model.id}: exhausted {attempts} attempts, last error: {last_error}")


_SUMMARIZE_SYSTEM_PROMPT = (
    "Summarize the following tool output in under 500 words. Preserve anything "
    "that looks like a flag, credential, error message, or file path verbatim."
)


def make_utility_summarizer(
    provider: Provider, model: ModelInfo, ledger: CostLedger
) -> Callable[[str], str]:
    """Wrap a provider+model+ledger as a plain ``(str) -> str`` summarizer.

    Every utility call (context.py's oversized-output / history-compaction
    summarization) goes through the same :func:`complete_with_retry` path as
    the main loop, into the same ledger — D5's "every LLM call, including
    utility calls, is provider-agnostic, retried, and costed."
    """

    def summarize(text: str) -> str:
        completion = complete_with_retry(
            provider,
            model,
            ledger,
            system=_SUMMARIZE_SYSTEM_PROMPT,
            messages=[Message(role="user", content=text)],
            tools=(),
            max_tokens=800,
        )
        return completion.text

    return summarize
