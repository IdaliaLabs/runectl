"""Anthropic adapter (D5, plan §3.4).

NOTE for reviewers: this adapter's shape was verified against the installed
`anthropic` SDK (message/usage field names, `messages.create` signature,
exception hierarchy) but was never exercised against the live API — no
Anthropic key was available in the build environment. See the M4 handoff
report before trusting it for a real run.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import anthropic
from anthropic.types import TextBlock, ToolUseBlock

from runectl.providers.base import (
    Completion,
    Message,
    ToolCallRequest,
    TransientProviderError,
    Usage,
)
from runectl.tools.schema import ToolSchema, to_anthropic

# 5xx/429/timeout — retried with backoff by complete_with_retry (D5).
_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
)


def _to_anthropic_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue  # system is a separate top-level param
        if message.role == "user":
            out.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                content.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                )
            out.append({"role": "assistant", "content": content})
        elif message.role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.content,
                        }
                    ],
                }
            )
    return out


class AnthropicProvider:
    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        client: anthropic.Anthropic | None = None,
        prompt_cache: bool = True,
    ) -> None:
        self._model_id = model_id
        self._client = client or anthropic.Anthropic(api_key=api_key)
        self._prompt_cache = prompt_cache

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> Completion:
        # D18 — top-level auto-caching puts the breakpoint on the last cacheable
        # block, which in an agent loop is the end of the growing message list.
        # Each step therefore reads the whole previous prefix (tools + system +
        # every earlier turn) from cache at a tenth the price instead of paying
        # full input rate to re-send it. Caching the system prompt alone would
        # not help much: it is well under the minimum cacheable prefix.
        extra: dict[str, Any] = (
            {"cache_control": {"type": "ephemeral"}} if self._prompt_cache else {}
        )
        try:
            response = self._client.messages.create(
                model=self._model_id,
                max_tokens=max_tokens,
                system=system,
                # The SDK's exact TypedDict unions are more precise than we need here;
                # these are plain JSON-schema-shaped dicts matching the documented wire format.
                messages=cast(Any, _to_anthropic_messages(messages)),
                tools=cast(Any, to_anthropic(tools)),
                **cast(Any, extra),
            )
        except _TRANSIENT_ERRORS as exc:
            raise TransientProviderError(str(exc)) from exc

        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        for block in response.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                raw_input = block.input
                tool_calls.append(
                    ToolCallRequest(
                        id=block.id,
                        name=block.name,
                        arguments=raw_input if isinstance(raw_input, dict) else {},
                    )
                )

        return Completion(
            text="".join(text_parts),
            tool_calls=tuple(tool_calls),
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_read_tokens=response.usage.cache_read_input_tokens or 0,
                cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
            ),
            stop_reason=response.stop_reason or "unknown",
        )
