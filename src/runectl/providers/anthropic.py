"""Anthropic adapter (D5, plan §3.4).

Verified against the live API as of 2026-09-09: the ten-challenge bench ran
through this adapter (`bench/results/README.md`), and its error handling was
exercised too — a rejected key raises a clean UsageError (exit 6) and other API
errors a ProviderError (exit 5), see `providers/base.py` and
`tests/unit/test_provider_errors.py`. The message/usage field names and
`messages.create` signature were validated against the installed `anthropic` SDK.

Extended thinking (D20, added 2026-09-09): uses adaptive thinking
(``thinking={"type": "adaptive"}``) plus ``output_config={"effort": ...}`` for
the level, on Opus 5 / Sonnet 5 / Haiku's peers in this tier. Two things a
prior-generation implementation would get wrong:

- ``budget_tokens`` is **not sent**. It is rejected with a 400 on both Opus 5
  and Sonnet 5 — the two Anthropic models this registry marks
  ``supports_thinking=True`` — and is only a Haiku-era concept, and Haiku is
  the utility model, which never thinks (`registry.py`).
- ``display: "summarized"`` is **always set** when thinking is requested. The
  API's own default is ``"omitted"``, which returns ``thinking`` blocks with
  an empty ``.thinking`` string — enabling thinking without this flag would
  spend the tokens and capture nothing.

Thinking blocks, when present, must be replayed back **first** in the assistant
content list on the next turn, ahead of any text or ``tool_use`` block — that
ordering is this API's own requirement, not a rendering choice.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import anthropic
from anthropic.types import TextBlock, ThinkingBlock, ToolUseBlock

from runectl.providers.base import (
    Completion,
    Message,
    ToolCallRequest,
    TransientProviderError,
    Usage,
    api_error,
    auth_error,
)
from runectl.providers.registry import ThinkingLevel
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
            # D20 — thinking blocks must come first in the assistant content
            # list, before text and tool_use, replayed back exactly as the API
            # returned them. This is the API's own ordering requirement.
            content.extend(message.thinking_blocks)
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
        thinking: ThinkingLevel = "off",
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
        # D20 — adaptive thinking + an effort level, never budget_tokens (see
        # module docstring: budget_tokens is rejected outright on this model
        # tier). display="summarized" is mandatory or thinking content comes
        # back empty.
        if thinking != "off":
            extra["thinking"] = {"type": "adaptive", "display": "summarized"}
            extra["output_config"] = {"effort": thinking}
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
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise auth_error("Anthropic", exc) from exc
        except anthropic.APIError as exc:
            # Any other SDK error (400/404/422, an unexpected 4xx, a connection
            # error not in the transient set): clean ProviderError, not a traceback.
            raise api_error("Anthropic", exc) from exc

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        thinking_blocks: list[dict[str, Any]] = []
        tool_calls: list[ToolCallRequest] = []
        for block in response.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ThinkingBlock):
                thinking_parts.append(block.thinking)
                # Stored verbatim (as the API returned it) for replay — see
                # _to_anthropic_messages, which puts these back first.
                thinking_blocks.append(block.model_dump())
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
            thinking_text="\n".join(thinking_parts),
            thinking_blocks=tuple(thinking_blocks),
        )
