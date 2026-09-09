"""OpenAI adapter (D5, plan §3.4).

NOTE for reviewers: this adapter's shape was verified against the installed
`openai` SDK (chat completion / usage field names, `chat.completions.create`
signature, exception hierarchy) but was never exercised against the live API —
no OpenAI key was available in the build environment. See the M4 handoff
report before trusting it for a real run.
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


class OpenAIProvider:
    def __init__(self, *, model_id: str, api_key: str, client: openai.OpenAI | None = None) -> None:
        self._model_id = model_id
        self._client = client or openai.OpenAI(api_key=api_key)

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> Completion:
        try:
            response = self._client.chat.completions.create(
                model=self._model_id,
                # Plain JSON-shaped dicts matching the documented wire format; the SDK's
                # exact TypedDict unions are more precise than we need to express here.
                messages=cast(Any, _to_openai_messages(system, messages)),
                tools=cast(Any, to_openai(tools)),
                max_completion_tokens=max_tokens,
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
            usage=Usage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            ),
            stop_reason=choice.finish_reason or "unknown",
        )
