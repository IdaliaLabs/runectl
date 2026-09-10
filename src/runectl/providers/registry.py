"""Model registry: provider/capability metadata, never string-prefix sniffing (D5, plan §3.2).

``--model`` is required; the provider and every capability (tool support, prompt
caching, context window, price, thinking support) come from this table. Adding a
fourth provider is a registry entry plus an adapter, not a redesign (D5's
"assumptions" note).

Pricing/availability snapshot 2026-09-07, Anthropic rows verified against a live
`client.models.list()` call plus the published price table on that date. The
earlier 2026-09-05 figures were wrong: Opus 5 was listed at 15/75 (actually
5/25), Sonnet 5 at 3/15 (actually 2/10), and both at a 200K context window
(actually 1M). OpenAI and Google rows remain unverified estimates — re-check
them before trusting a cost report for those providers.

Cache multipliers (D18): a cache *write* bills at 1.25x `price_in`, a cache
*read* at 0.10x. Both are provider-standard for Anthropic today and are applied
in `cost.py`, not stored per-model.

Thinking (D20, added 2026-09-09): ``claude-haiku-4-5-20251001`` is renamed to
``claude-haiku-4-5`` here — current Anthropic model ids carry no date suffix,
and the dated string was a stale-prior artifact. It stays the default utility
model (`cheapest_model_for`) and is marked ``supports_thinking=False`` — a
model whose only job is cheap summarization should not think.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ProviderName = Literal["anthropic", "openai", "google"]

# The one CLI-facing thinking vocabulary, shared across all three providers
# (D20). "off" means no thinking requested at all. Ordered low to high so a
# clamp can be computed by index.
ThinkingLevel = Literal["off", "low", "medium", "high", "xhigh", "max"]
THINKING_LEVELS: tuple[ThinkingLevel, ...] = ("off", "low", "medium", "high", "xhigh", "max")

# How a provider's own API represents a non-"off" thinking level. "none" means
# the model has no thinking support at all — `--thinking` other than `off` is
# a hard usage error (exit 6) for such a model, not a silent no-op.
ThinkingStyle = Literal["anthropic_adaptive", "openai_effort", "google_budget", "none"]


class ModelInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    provider: ProviderName
    context_window: int
    supports_tools: bool
    supports_prompt_cache: bool
    price_in: float  # USD per 1M input tokens
    price_out: float  # USD per 1M output tokens

    # D18 — how much of `price_in` a cached token costs. Anthropic-standard
    # today; if a provider ever differs these become per-model fields.
    cache_write_multiplier: float = 1.25
    cache_read_multiplier: float = 0.10

    # D20 — extended thinking. `thinking_style` selects how an adapter maps
    # the shared `ThinkingLevel` vocabulary onto that provider's own API;
    # `supports_thinking=False` makes `thinking_style` irrelevant ("none").
    supports_thinking: bool = False
    thinking_style: ThinkingStyle = "none"
    # The highest level this model can actually reach, e.g. a model whose API
    # tops out below "max". Requests above this are clamped down and the
    # clamp is recorded in the trace (D20) — never silently substituted.
    max_thinking_level: ThinkingLevel = "off"


MODEL_REGISTRY: dict[str, ModelInfo] = {
    "claude-opus-5": ModelInfo(
        id="claude-opus-5", provider="anthropic", context_window=1_000_000,
        supports_tools=True, supports_prompt_cache=True, price_in=5.0, price_out=25.0,
        supports_thinking=True, thinking_style="anthropic_adaptive", max_thinking_level="max",
    ),
    "claude-sonnet-5": ModelInfo(
        id="claude-sonnet-5", provider="anthropic", context_window=1_000_000,
        supports_tools=True, supports_prompt_cache=True, price_in=2.0, price_out=10.0,
        supports_thinking=True, thinking_style="anthropic_adaptive", max_thinking_level="max",
    ),
    "claude-haiku-4-5": ModelInfo(
        id="claude-haiku-4-5", provider="anthropic", context_window=200_000,
        supports_tools=True, supports_prompt_cache=True, price_in=1.0, price_out=5.0,
        supports_thinking=False, thinking_style="none", max_thinking_level="off",
    ),
    "gpt-5": ModelInfo(
        id="gpt-5", provider="openai", context_window=272_000,
        supports_tools=True, supports_prompt_cache=True, price_in=5.0, price_out=15.0,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="max",
    ),
    "gpt-5-mini": ModelInfo(
        id="gpt-5-mini", provider="openai", context_window=272_000,
        supports_tools=True, supports_prompt_cache=True, price_in=0.5, price_out=1.5,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="high",
    ),
    "gemini-2.5-pro": ModelInfo(
        id="gemini-2.5-pro", provider="google", context_window=1_000_000,
        supports_tools=True, supports_prompt_cache=True, price_in=1.25, price_out=10.0,
        # google-genai's ThinkingLevel enum tops out at HIGH (LOW/MEDIUM/HIGH,
        # no xhigh/max) — requests above "high" are clamped by
        # resolve_thinking_level() rather than sent to an enum value that
        # doesn't exist.
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
    ),
    "gemini-2.5-flash": ModelInfo(
        id="gemini-2.5-flash", provider="google", context_window=1_000_000,
        supports_tools=True, supports_prompt_cache=False, price_in=0.3, price_out=2.5,
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
    ),
}


class UnknownModelError(Exception):
    pass


def resolve(model_id: str) -> ModelInfo:
    info = MODEL_REGISTRY.get(model_id)
    if info is None:
        raise UnknownModelError(
            f"unknown model {model_id!r} — not in the model registry "
            "(providers/registry.py). D5: providers are never inferred from a "
            "string prefix, so an unlisted model id is a hard error, not a guess."
        )
    return info


def cheapest_model_for(provider: ProviderName) -> ModelInfo:
    """Default --utility-model: the cheapest model of the same provider as --model."""
    candidates = [m for m in MODEL_REGISTRY.values() if m.provider == provider]
    if not candidates:
        raise UnknownModelError(f"no registered models for provider {provider!r}")
    return min(candidates, key=lambda m: m.price_in + m.price_out)


def resolve_thinking_level(model: ModelInfo, requested: ThinkingLevel) -> tuple[ThinkingLevel, bool]:
    """Resolve a requested --thinking level against a model's real ceiling (D20).

    Returns ``(resolved_level, was_clamped)``. A model with no thinking support
    clamps any non-"off" request down to "off" rather than erroring — a run
    that asked for thinking on a model that can't do it still runs, it just
    doesn't get to think, and the clamp is recorded in `run.started`/`run.json`
    so that degradation is never silent (D20, D11's "loud, not silent" posture).
    """
    if requested == "off":
        return "off", False
    if not model.supports_thinking:
        return "off", True
    if THINKING_LEVELS.index(requested) > THINKING_LEVELS.index(model.max_thinking_level):
        return model.max_thinking_level, True
    return requested, False
