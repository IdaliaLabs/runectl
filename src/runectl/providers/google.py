"""Google (Gemini) adapter (D5).

NOTE for reviewers: this adapter's shape was verified against the installed
`google-genai` SDK (types, field names, `models.generate_content` signature,
exception hierarchy) but was never exercised against the live API — no Google
key was available in the build environment, and Gemini's function-calling
message shape (role mapping for tool results in particular) is the least
certain of the three adapters. See the M4 handoff report before trusting it
for a real run.

Extended thinking (D20, added 2026-09-09): **request-side only, unverified
against a live service, same as the rest of this adapter.** `google-genai`'s
`ThinkingConfig.thinking_level` enum is `LOW`/`MEDIUM`/`HIGH` only (no
xhigh/max — the registry's `max_thinking_level="high"` for both Gemini models
reflects that; `resolve_thinking_level` clamps before this adapter ever sees a
level outside that set) and `include_thoughts=True` is required to get thought
summaries back at all. A returned `Part` with `.thought` true carries a thought
summary in `.text` and an opaque `.thought_signature` for replay continuity —
handled the same way Anthropic's thinking-block signature is (see
`anthropic.py`), by round-tripping it back unchanged rather than inspecting it.
"""

from __future__ import annotations

import base64
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
    api_error,
    auth_error,
)
from runectl.providers.registry import ThinkingLevel
from runectl.tools.schema import ToolSchema, to_google

_THINKING_LEVEL_MAP: dict[ThinkingLevel, types.ThinkingLevel] = {
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}


def _is_transient(exc: genai_errors.APIError) -> bool:
    if isinstance(exc, genai_errors.ServerError):
        return True
    return isinstance(exc, genai_errors.ClientError) and exc.code == 429


def _thought_part_to_dict(part: types.Part) -> dict[str, object]:
    """Store a thought ``Part`` as a JSON-safe dict (D20).

    ``thought_signature`` is opaque bytes that are not guaranteed to be valid
    UTF-8, so it is base64-encoded here rather than handed to pydantic's plain
    ``model_dump()`` — a raw non-UTF-8 ``bytes`` value inside an ``Any``-typed
    dict field raises ``UnicodeDecodeError`` the moment anything (the trace
    writer, the cassette recorder) tries to serialize it as JSON.
    """
    signature = part.thought_signature
    return {
        "thought": True,
        "text": part.text or "",
        "thought_signature_b64": base64.b64encode(signature).decode("ascii") if signature else None,
    }


def _dict_to_thought_part(data: dict[str, object]) -> types.Part:
    signature_b64 = data.get("thought_signature_b64")
    signature = base64.b64decode(str(signature_b64)) if signature_b64 else None
    return types.Part(thought=True, text=str(data.get("text", "")), thought_signature=signature)


def _to_google_contents(messages: Sequence[Message]) -> list[types.Content]:
    out: list[types.Content] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "user":
            out.append(types.Content(role="user", parts=[types.Part(text=message.content)]))
        elif message.role == "assistant":
            parts: list[types.Part] = []
            # D20 — thought parts must be replayed back first, ahead of the
            # visible text and function calls, for continuity across turns.
            parts.extend(_dict_to_thought_part(d) for d in message.thinking_blocks)
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


def _usage(usage: types.GenerateContentResponseUsageMetadata | None) -> Usage:
    """Account for cached and thinking tokens, both of which were being dropped (D18).

    Fixed 2026-09-11. Two separate leaks, in opposite directions:

    - `cached_content_token_count` was ignored, so a cache hit was billed at the
      full input rate — `Usage.input_tokens` is documented as uncached input
      only, and `prompt_token_count` is the total.
    - `thoughts_token_count` was ignored, and Gemini reports thinking tokens
      *outside* `candidates_token_count` while still billing them at the output
      rate. With thinking on, every reasoning token was free as far as the
      ledger knew — the understatement grew with exactly the setting that makes
      a run expensive.

    Google does not bill cache writes separately, so `cache_write_tokens` stays
    zero.
    """
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0)
    cached = usage.cached_content_token_count or 0
    prompt_tokens = usage.prompt_token_count or 0
    return Usage(
        # max(): a negative count would silently credit the ledger.
        input_tokens=max(prompt_tokens - cached, 0),
        output_tokens=(usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
        cache_read_tokens=cached,
    )


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
        thinking: ThinkingLevel = "off",
    ) -> Completion:
        thinking_config = None
        if thinking != "off":
            level = _THINKING_LEVEL_MAP.get(thinking)
            if level is not None:
                thinking_config = types.ThinkingConfig(include_thoughts=True, thinking_level=level)
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            tools=[types.Tool(function_declarations=_function_declarations(tools))],
            thinking_config=thinking_config,
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
            if isinstance(exc, genai_errors.ClientError) and exc.code in (401, 403):
                raise auth_error("Google", exc) from exc
            # Any other API error (400/404/422, an unexpected client/server error):
            # a clean ProviderError, never a raw traceback.
            raise api_error("Google", exc) from exc

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        thinking_blocks: list[dict[str, object]] = []
        tool_calls: list[ToolCallRequest] = []
        candidates = response.candidates or []
        parts = candidates[0].content.parts if candidates and candidates[0].content else None
        for part in parts or []:
            if part.thought:
                # A thought part's `.text` is the thought summary, not the
                # visible reply — routing it into text_parts would leak the
                # model's reasoning into the conversation as if it had said it.
                if part.text:
                    thinking_parts.append(part.text)
                thinking_blocks.append(_thought_part_to_dict(part))
            elif part.text:
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
            usage=_usage(usage),
            stop_reason=str(finish_reason) if finish_reason is not None else "unknown",
            thinking_text="\n".join(thinking_parts),
            thinking_blocks=tuple(thinking_blocks),
        )
