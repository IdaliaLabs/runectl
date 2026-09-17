"""OpenAI adapter (D5), on the Responses API (`v1/responses`).

Moved off Chat Completions on 2026-09-16. Chat Completions refused function
tools on four models (`gpt-6-astra`, the `gpt-5.6` family) at every reasoning
setting, and never returned reasoning content, so `Completion.thinking_text` was
always empty there (STATUS 2026-09-13). Responses lifts the tools restriction
and returns reasoning as first-class output items, which round-trip on the next
turn — the OpenAI shape of D20's "thinking state must round-trip per each
provider's contract", the same rule Anthropic's thinking blocks and Google's
`thought_signature` already follow.

Statelessness (`store=false`): runectl owns the conversation, so every request
replays the whole history — including the reasoning and function-call items from
earlier turns, carried on the assistant `Message`'s `thinking_blocks`/`tool_calls`
and emitted back ahead of the next user turn. `include=["reasoning.encrypted_content"]`
asks the API for the reasoning blob needed to replay faithfully.

Effort: `reasoning.effort` takes the shared level. "off" reaching this adapter
means the registry says the model can be stopped (`thinking_off_supported`) — it
is sent as `effort:"none"`; a model that thinks regardless never sees "off"
(`resolve_thinking_level` clamped it up and recorded the clamp). No OpenAI model
accepts `max`; the registry caps each row (live-probed, not read from docs).
`reasoning.summary` is requested for readable thinking text and dropped on the
one 400 that means the organization is unverified for summaries.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

import openai

from runectl.providers.base import (
    Completion,
    Message,
    ToolCallRequest,
    Usage,
    api_error,
    auth_error,
    transient_or_billing,
)
from runectl.providers.registry import MIN_REAL_THINKING_LEVEL, ThinkingLevel
from runectl.tools.schema import ToolSchema, to_openai_responses

_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


# Fields a reasoning item carries on *output* that the API rejects on *input*.
# Found live 2026-09-16: replaying `status` gave `Unknown parameter input[N].status`.
_REASONING_OUTPUT_ONLY = frozenset({"status"})


def _reasoning_for_input(block: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in block.items() if k not in _REASONING_OUTPUT_ONLY and v is not None}


def _to_responses_input(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Canonical messages as Responses `input` items (system goes to `instructions`).

    An assistant turn replays its reasoning items first, then any text, then its
    function calls — the order the API returned them and requires back. A wholly
    empty assistant turn contributes nothing, which is the empty-turn skip other
    adapters make explicit.
    """
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "user":
            items.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            items.extend(_reasoning_for_input(block) for block in message.thinking_blocks)
            if message.content:
                items.append({"role": "assistant", "content": message.content})
            for call in message.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    }
                )
        elif message.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
    return items


def _usage(usage: Any) -> Usage:
    """Split Responses usage into cached / uncached / reasoning halves (D18).

    `input_tokens` is the whole prompt, cached included; `input_tokens_details`
    carries `cached_tokens` (read) and `cache_write_tokens`. `output_tokens`
    already includes reasoning tokens, so it is not adjusted.
    """
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0)
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    cache_write = getattr(details, "cache_write_tokens", 0) or 0
    input_tokens = usage.input_tokens or 0
    return Usage(
        # max(): defensive against more cached than total, which would credit the ledger.
        input_tokens=max(input_tokens - cached, 0),
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=cached,
        cache_write_tokens=cache_write,
    )


def _is_summary_unverified(exc: openai.APIError) -> bool:
    """A 400 that means the org is not verified for reasoning summaries — the one
    case where dropping `reasoning.summary` and retrying is the right move."""
    text = str(exc).lower()
    return "summary" in text and ("verif" in text or "not supported" in text or "unsupported" in text)


class OpenAIProvider:
    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        client: openai.OpenAI | None = None,
        supports_thinking: bool = False,
        thinking_off_supported: bool = True,
    ) -> None:
        self._model_id = model_id
        # max_retries=0: runectl owns retry (D5); the SDK default of 2 would nest.
        self._client = client or openai.OpenAI(api_key=api_key, max_retries=0)
        self._supports_thinking = supports_thinking
        # Whether this model accepts `effort:"none"` at all — NOT the same fact as
        # supports_thinking (the gpt-5 family thinks but refuses "none").
        self._thinking_off_supported = thinking_off_supported

    def _reasoning(self, thinking: ThinkingLevel) -> dict[str, Any] | None:
        if thinking != "off":
            return {"effort": thinking, "summary": "auto"}
        if self._supports_thinking:
            effort = "none" if self._thinking_off_supported else MIN_REAL_THINKING_LEVEL
            return {"effort": effort, "summary": "auto"}
        return None

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
        thinking: ThinkingLevel = "off",
    ) -> Completion:
        reasoning = self._reasoning(thinking)
        request: dict[str, Any] = {
            "model": self._model_id,
            "instructions": system,
            "input": cast(Any, _to_responses_input(messages)),
            "tools": cast(Any, to_openai_responses(tools)),
            "max_output_tokens": max_tokens,
            "store": False,
        }
        if reasoning is not None:
            request["reasoning"] = reasoning
            request["include"] = ["reasoning.encrypted_content"]

        try:
            response = self._client.responses.create(**request)
        except openai.APIError as first_error:
            # One retry, without the reasoning summary, when the org can't get them.
            if reasoning is not None and _is_summary_unverified(first_error):
                request["reasoning"] = {"effort": reasoning["effort"]}
                try:
                    response = self._client.responses.create(**request)
                except _TRANSIENT_ERRORS as exc:
                    raise transient_or_billing("OpenAI", exc) from exc
                except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
                    raise auth_error("OpenAI", exc) from exc
                except openai.APIError as exc:
                    raise api_error("OpenAI", exc) from exc
            elif isinstance(first_error, _TRANSIENT_ERRORS):
                raise transient_or_billing("OpenAI", first_error) from first_error
            elif isinstance(first_error, openai.AuthenticationError | openai.PermissionDeniedError):
                raise auth_error("OpenAI", first_error) from first_error
            else:
                raise api_error("OpenAI", first_error) from first_error

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        thinking_blocks: list[dict[str, Any]] = []
        tool_calls: list[ToolCallRequest] = []
        for item in response.output:
            kind = getattr(item, "type", None)
            if kind == "message":
                for block in getattr(item, "content", None) or []:
                    if getattr(block, "type", None) == "output_text":
                        text_parts.append(block.text)
            elif kind == "function_call":
                try:
                    arguments = json.loads(item.arguments) if item.arguments else {}
                except json.JSONDecodeError:
                    arguments = {}
                tool_calls.append(
                    ToolCallRequest(id=item.call_id, name=item.name, arguments=arguments)
                )
            elif kind == "reasoning":
                # Round-tripped verbatim on the next turn (see _to_responses_input);
                # the summary, when present, is the readable reasoning text.
                thinking_blocks.append(item.model_dump())
                for summary in getattr(item, "summary", None) or []:
                    if getattr(summary, "text", None):
                        thinking_parts.append(summary.text)

        return Completion(
            text="".join(text_parts),
            tool_calls=tuple(tool_calls),
            usage=_usage(response.usage),
            stop_reason=response.status or "unknown",
            thinking_text="\n".join(thinking_parts),
            thinking_blocks=tuple(thinking_blocks),
        )
