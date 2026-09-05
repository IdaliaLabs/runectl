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
from runectl.errors import ProviderError
from runectl.providers.cost import CostLedger
from runectl.providers.registry import ModelInfo
from runectl.tools.schema import ToolSchema


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int


class Message(BaseModel):
    """One canonical chat message; each adapter derives its own wire shape."""

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_call_id: str | None = None  # set when role == "tool"
    # set when role == "tool" — Google's FunctionResponse needs the name, not just an id
    tool_name: str | None = None
    tool_calls: tuple[ToolCallRequest, ...] = ()  # set when role == "assistant" and it called a tool


class Completion(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    tool_calls: tuple[ToolCallRequest, ...]
    usage: Usage
    stop_reason: str


class TransientProviderError(Exception):
    """Raised by an adapter for a 429/5xx/timeout. Retried with backoff; never
    escapes :func:`complete_with_retry` — callers only ever see :class:`ProviderError`."""


class Provider(Protocol):
    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
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
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> Completion:
    last_error: TransientProviderError | None = None
    for attempt in range(attempts):
        try:
            completion = provider.complete(
                system=system, messages=messages, tools=tools, max_tokens=max_tokens
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
        )
        return completion
    raise ProviderError(f"{model.id}: exhausted {attempts} attempts, last error: {last_error}")
