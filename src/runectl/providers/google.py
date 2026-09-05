"""Google (Gemini) adapter (D5, plan §3.4).

NOTE for reviewers: this adapter's shape was verified against the installed
`google-genai` SDK (types, field names, `models.generate_content` signature,
exception hierarchy) but was never exercised against the live API — no Google
key was available in the build environment, and Gemini's function-calling
message shape (role mapping for tool results in particular) is the least
certain of the three adapters. See the M4 handoff report before trusting it
for a real run.
"""

from __future__ import annotations

from collections.abc import Sequence

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from runectl.providers.base import (
    Completion,
    Message,
    ToolCallRequest,
    TransientProviderError,
    Usage,
)
from runectl.tools.schema import ToolSchema, to_google


def _is_transient(exc: genai_errors.APIError) -> bool:
    if isinstance(exc, genai_errors.ServerError):
        return True
    return isinstance(exc, genai_errors.ClientError) and exc.code == 429


def _to_google_contents(messages: Sequence[Message]) -> list[types.Content]:
    out: list[types.Content] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "user":
            out.append(types.Content(role="user", parts=[types.Part(text=message.content)]))
        elif message.role == "assistant":
            parts: list[types.Part] = []
            if message.content:
                parts.append(types.Part(text=message.content))
            for call in message.tool_calls:
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(id=call.id, name=call.name, args=call.arguments)
                    )
                )
            out.append(types.Content(role="model", parts=parts))
        elif message.role == "tool":
            out.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=message.tool_call_id,
                                name=message.tool_name or "",
                                response={"output": message.content},
                            )
                        )
                    ],
                )
            )
    return out


def _function_declarations(tools: Sequence[ToolSchema]) -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name=raw["name"],
            description=raw["description"],
            parameters_json_schema=raw["parameters"],
        )
        for raw in to_google(tools)
    ]


class GoogleProvider:
    def __init__(self, *, model_id: str, api_key: str, client: genai.Client | None = None) -> None:
        self._model_id = model_id
        self._client = client or genai.Client(api_key=api_key)

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> Completion:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            tools=[types.Tool(function_declarations=_function_declarations(tools))],
        )
        try:
            response = self._client.models.generate_content(
                model=self._model_id,
                contents=_to_google_contents(messages),
                config=config,
            )
        except genai_errors.APIError as exc:
            if _is_transient(exc):
                raise TransientProviderError(str(exc)) from exc
            raise

        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        candidates = response.candidates or []
        parts = candidates[0].content.parts if candidates and candidates[0].content else None
        for part in parts or []:
            if part.text:
                text_parts.append(part.text)
            if part.function_call is not None:
                call = part.function_call
                tool_calls.append(
                    ToolCallRequest(id=call.id or "", name=call.name or "", arguments=dict(call.args or {}))
                )

        usage = response.usage_metadata
        finish_reason = candidates[0].finish_reason if candidates else None

        return Completion(
            text="".join(text_parts),
            tool_calls=tuple(tool_calls),
            usage=Usage(
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            ),
            stop_reason=str(finish_reason) if finish_reason is not None else "unknown",
        )
