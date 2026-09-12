"""OpenAI adapter (D5, plan §3.4).

NOTE for reviewers: this adapter's shape was verified against the installed
`openai` SDK (chat completion / usage field names, `chat.completions.create`
signature, exception hierarchy) but was never exercised against the live API —
no OpenAI key was available in the build environment. See the M4 handoff
report before trusting it for a real run.

Extended thinking (D20, added 2026-09-09): **request-side only, unverified
against a live service, same as the rest of this adapter.** The installed SDK
(3.8.0) exposes a top-level `reasoning_effort` parameter on
`chat.completions.create` accepting `none`/`minimal`/`low`/`medium`/`high`/
`xhigh`/`max`, which `low`/`medium`/`high`/`xhigh`/`max` here map onto
directly (`off` sends nothing, leaving the model's own default). **This API
surface never returns reasoning content** — `ChatCompletionMessage` has no
`reasoning`/`thinking` field, unlike the Responses API's encrypted reasoning
items. `Completion.thinking_text` is therefore always empty for this adapter
even when a level was requested and honored server-side; a switch to the
Responses API would be required to render OpenAI's reasoning, which is out of
scope here (D5 didn't require it, and Chat Completions is what the rest of
this adapter already uses).
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
    TransientProviderError,
    Usage,
    api_error,
    auth_error,
)
from runectl.providers.registry import ThinkingLevel
from runectl.tools.schema import ToolSchema, to_openai

_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


def _to_openai_messages(system: str, messages: Sequence[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            out.append(
                {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
            )
        elif message.role == "assistant" and message.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                        }
                        for call in message.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": message.role, "content": message.content})
    return out


def _usage(usage: Any) -> Usage:
    """Split OpenAI's `prompt_tokens` into its cached and uncached halves (D18).

    Fixed 2026-09-11. `prompt_tokens` is the *total* prompt size, cached tokens
    included, and this adapter used to assign it straight to `Usage.input_tokens`
    — whose own docstring says it is uncached input only. Every cache hit was
    therefore billed at the full input rate, overstating the cost of exactly the
    runs prompt caching is supposed to make cheap. The cached count lives in
    `usage.prompt_tokens_details.cached_tokens`.

    OpenAI does not bill cache writes, so `cache_write_tokens` stays zero.
    """
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0)
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    prompt_tokens = usage.prompt_tokens or 0
    return Usage(
        # max(): defensive against a provider reporting more cached tokens than
        # prompt tokens. A negative count would silently credit the ledger.
        input_tokens=max(prompt_tokens - cached, 0),
        output_tokens=usage.completion_tokens or 0,
        cache_read_tokens=cached,
    )


class OpenAIProvider:
    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        client: openai.OpenAI | None = None,
        supports_thinking: bool = False,
    ) -> None:
        self._model_id = model_id
        self._client = client or openai.OpenAI(api_key=api_key)
        # D20 amendment 2026-09-11 — needed to tell two different "off"s apart:
        # a reasoning model must be told `reasoning_effort: "none"` to stop
        # thinking (its own default is `medium`), while a non-reasoning model
        # rejects the parameter outright. Mirrors how `prompt_cache` is already
        # passed down from the registry row by `run_cmd._build_provider`.
        self._supports_thinking = supports_thinking

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
        thinking: ThinkingLevel = "off",
    ) -> Completion:
        # D20 — "off" reaching here means the registry says this model can
        # actually be stopped from thinking (`thinking_off_supported`); a model
        # that thinks regardless never sees "off", because
        # `resolve_thinking_level` has already clamped it up to "low" and
        # recorded the clamp. On a reasoning model, stopping it means saying so:
        # `reasoning_effort: "none"`, since omitting the parameter leaves the
        # model's own default (medium) in force. A non-reasoning model rejects
        # the parameter, so it gets nothing.
        extra: dict[str, Any] = {}
        if thinking != "off":
            extra["reasoning_effort"] = thinking
        elif self._supports_thinking:
            extra["reasoning_effort"] = "none"
        try:
            response = self._client.chat.completions.create(
                model=self._model_id,
                # Plain JSON-shaped dicts matching the documented wire format; the SDK's
                # exact TypedDict unions are more precise than we need to express here.
                messages=cast(Any, _to_openai_messages(system, messages)),
                tools=cast(Any, to_openai(tools)),
                max_completion_tokens=max_tokens,
                **extra,
            )
        except _TRANSIENT_ERRORS as exc:
            raise TransientProviderError(str(exc)) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise auth_error("OpenAI", exc) from exc
        except openai.APIError as exc:
            raise api_error("OpenAI", exc) from exc

        choice = response.choices[0]
        tool_calls: list[ToolCallRequest] = []
        for call in choice.message.tool_calls or []:
            function = getattr(call, "function", None)
            if function is None:
                continue
            try:
                arguments = json.loads(function.arguments) if function.arguments else {}
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(ToolCallRequest(id=call.id, name=function.name, arguments=arguments))

        usage = response.usage
        return Completion(
            text=choice.message.content or "",
            tool_calls=tuple(tool_calls),
            usage=_usage(usage),
            stop_reason=choice.finish_reason or "unknown",
        )
