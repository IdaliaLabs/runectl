"""Model registry: provider/capability metadata, never string-prefix sniffing (D5).

``--model`` is required; the provider and every capability (prompt caching,
context window, price, thinking support) come from this table. Adding a
fourth provider is a registry entry plus an adapter, not a redesign (D5's
"assumptions" note).

Every price block below carries the date it was checked and the URL it came
from. Availability and pricing rot fast, and a wrong price here is not a
cosmetic bug: it silently corrupts `cost.updated`, `run.json` and every number
`runectl bench` prints. Re-check a block before trusting a cost report from it.

History worth keeping:

- 2026-09-05 figures for Anthropic were wrong (Opus 5 listed at 15/75, actually
  5/25; Sonnet 5 at 3/15, actually 2/10; both at 200K context, actually 1M),
  corrected 2026-09-07 against a live ``client.models.list()`` call.
- 2026-09-11: the OpenAI rows were wrong too, and by more. ``gpt-5`` was listed
  at 5.00/15.00 and is actually 1.25/10.00; ``gpt-5-mini`` at 0.50/1.50 and is
  actually 0.25/2.00. Both had been carried since 2026-09-05 as "unverified
  estimates" and never re-checked. The registry also stood three generations
  behind on OpenAI and two on Google, which meant none of the cheap tiers a
  competition actually runs on — ``gpt-5-nano``, ``gemini-2.5-flash-lite``,
  ``gpt-5.6-luna`` — could be selected at all. Grown 7 -> 37 rows on that pass.

Cache pricing (D18, amended 2026-09-11): a cache *write* bills at 1.25x
``price_in`` and lives as a constant in `cost.py`; no provider here differs. A
cache *read* is now the per-model ``cache_read_multiplier`` field, because
OpenAI genuinely does differ — the ``gpt-5``+ families read at 0.10x, but
``gpt-4.1*`` reads at 0.25x and ``gpt-4o*`` at 0.50x. Pricing one of those at a
flat 0.10x understated the cached portion of a run by up to 5x.

Thinking (D20, added 2026-09-09; amended 2026-09-11). Two things to know:

- ``claude-haiku-4-5`` is spelled without a date suffix — current Anthropic
  model ids carry none, and the dated string was a stale-prior artifact. It is
  the default utility model (`cheapest_model_for`) and is marked
  ``supports_thinking=False``: it is an *extended*-thinking-only model, which
  rejects the ``adaptive`` config this codebase's Anthropic adapter sends, and
  a model whose only job is cheap summarization should not think anyway. Every
  other extended-thinking-only Anthropic model (Opus 4.5, Sonnet 4.5 and
  earlier) is deliberately absent for the same reason — supporting them means a
  second thinking mode in the adapter, which is not worth it.
- ``thinking_off_supported`` exists because **on most current models, sending no
  thinking configuration does not mean the model does not think.** Anthropic
  documents Sonnet 5 and Opus 5 as thinking by default and Fable 5/5.1 as always
  on; Gemini 3.x and 2.5 think by default except ``flash-lite``; OpenAI's
  reasoning models default to ``medium`` effort unless told ``none``. Before
  this field, ``--thinking off`` sent nothing, the model thought and billed, and
  the trace recorded ``thinking_level="off"`` with no clamp — a silent
  degradation of exactly the kind D20 and D11 exist to prevent. See
  `resolve_thinking_level`.
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

# What a model that cannot be made to stop thinking is asked for instead of
# "off" — the cheapest level that is a real request, so the spend is at least
# the smallest it can be and is recorded rather than defaulted to silently.
MIN_REAL_THINKING_LEVEL: ThinkingLevel = "low"

# How a provider's own API represents a non-"off" thinking level. "none" means
# the model has no thinking support at all — `--thinking` other than `off` is
# a hard usage error (exit 6) for such a model, not a silent no-op.
ThinkingStyle = Literal["anthropic_adaptive", "openai_effort", "google_budget", "none"]


class ModelInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    provider: ProviderName
    context_window: int
    supports_prompt_cache: bool
    price_in: float  # USD per 1M input tokens
    price_out: float  # USD per 1M output tokens

    # D18 amendment 2026-09-11 — what one cached input token costs as a
    # fraction of `price_in`. Per-model because OpenAI's older families differ.
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
    # D20 amendment 2026-09-11 — whether "off" can actually be honored. False
    # for a model that thinks by default or always; see the module docstring.
    # Meaningless (and left True) when `supports_thinking` is False, since such
    # a model never thinks in the first place.
    thinking_off_supported: bool = True


MODEL_REGISTRY: dict[str, ModelInfo] = {
    # ---------------------------------------------------------------- anthropic
    # Checked 2026-09-11 against the bundled `claude-api` skill's model table
    # (itself cached 2026-06-24) and the per-model thinking-configuration table
    # at https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting
    # — which is where each row's `thinking_off_supported` comes from: Fable
    # 5/5.1 are "Always on", Opus 5 and Sonnet 5 default "On", and the 4.6/4.7/4.8
    # generation defaults "Off". Prices unchanged since the 2026-09-07 live
    # `client.models.list()` verification.
    #
    # The 4.6 generation has no `xhigh` effort level (it arrived with 4.7), so it
    # ceilings at "high" and `resolve_thinking_level` clamps a higher request down.
    "claude-fable-5-1": ModelInfo(
        id="claude-fable-5-1", provider="anthropic", context_window=1_000_000,
        supports_prompt_cache=True, price_in=10.0, price_out=50.0,
        supports_thinking=True, thinking_style="anthropic_adaptive", max_thinking_level="max",
        thinking_off_supported=False,
    ),
    "claude-fable-5": ModelInfo(
        id="claude-fable-5", provider="anthropic", context_window=1_000_000,
        supports_prompt_cache=True, price_in=10.0, price_out=50.0,
        supports_thinking=True, thinking_style="anthropic_adaptive", max_thinking_level="max",
        thinking_off_supported=False,
    ),
    "claude-opus-5": ModelInfo(
        id="claude-opus-5", provider="anthropic", context_window=1_000_000,
        supports_prompt_cache=True, price_in=5.0, price_out=25.0,
        supports_thinking=True, thinking_style="anthropic_adaptive", max_thinking_level="max",
        thinking_off_supported=False,
    ),
    "claude-opus-4-8": ModelInfo(
        id="claude-opus-4-8", provider="anthropic", context_window=1_000_000,
        supports_prompt_cache=True, price_in=5.0, price_out=25.0,
        supports_thinking=True, thinking_style="anthropic_adaptive", max_thinking_level="max",
    ),
    "claude-opus-4-7": ModelInfo(
        id="claude-opus-4-7", provider="anthropic", context_window=1_000_000,
        supports_prompt_cache=True, price_in=5.0, price_out=25.0,
        supports_thinking=True, thinking_style="anthropic_adaptive", max_thinking_level="max",
    ),
    "claude-opus-4-6": ModelInfo(
        id="claude-opus-4-6", provider="anthropic", context_window=1_000_000,
        supports_prompt_cache=True, price_in=5.0, price_out=25.0,
        supports_thinking=True, thinking_style="anthropic_adaptive", max_thinking_level="high",
    ),
    "claude-sonnet-5": ModelInfo(
        id="claude-sonnet-5", provider="anthropic", context_window=1_000_000,
        supports_prompt_cache=True, price_in=2.0, price_out=10.0,
        supports_thinking=True, thinking_style="anthropic_adaptive", max_thinking_level="max",
        thinking_off_supported=False,
    ),
    "claude-sonnet-4-6": ModelInfo(
        id="claude-sonnet-4-6", provider="anthropic", context_window=1_000_000,
        supports_prompt_cache=True, price_in=3.0, price_out=15.0,
        supports_thinking=True, thinking_style="anthropic_adaptive", max_thinking_level="high",
    ),
    "claude-haiku-4-5": ModelInfo(
        id="claude-haiku-4-5", provider="anthropic", context_window=200_000,
        supports_prompt_cache=True, price_in=1.0, price_out=5.0,
        supports_thinking=False, thinking_style="none", max_thinking_level="off",
    ),
    # ------------------------------------------------------------------- openai
    # Checked 2026-09-11 against https://developers.openai.com/api/docs/pricing
    # and, for the rows whose capabilities matter, that model's own page. Each
    # row below is served by `v1/chat/completions`, which is the endpoint this
    # adapter uses — verified directly for `gpt-6-astra`, `gpt-5.6-luna` and
    # `gpt-5-nano`.
    #
    # Deliberately absent: the `-pro` reasoning tiers (`gpt-5-pro`,
    # `gpt-5.4-pro`, `gpt-5.5-pro`) are Responses-API-only and priced at
    # 15/120 to 30/180, which is the wrong shape for an agent loop that makes a
    # call per step; and `gpt-3.5-turbo`, which wastes steps on agentic work.
    #
    # `reasoning_effort: "none"` is what actually turns thinking off here, and
    # only the `gpt-5*` families accept it — `gpt-6-astra` does not, so it is
    # `thinking_off_supported=False`. `gpt-4o*` and `gpt-4.1*` are not reasoning
    # models at all.
    #
    # Context windows are the documented total window. OpenAI caps *input* below
    # that (272K on the `gpt-5` family); the field is display-only, so the
    # documented figure is what is shown.
    "gpt-6-astra": ModelInfo(
        id="gpt-6-astra", provider="openai", context_window=1_050_000,
        supports_prompt_cache=True, price_in=10.0, price_out=50.0,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="max",
        thinking_off_supported=False,
    ),
    "gpt-5.6-sol": ModelInfo(
        id="gpt-5.6-sol", provider="openai", context_window=1_050_000,
        supports_prompt_cache=True, price_in=4.0, price_out=20.0,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="max",
    ),
    "gpt-5.6-terra": ModelInfo(
        id="gpt-5.6-terra", provider="openai", context_window=1_050_000,
        supports_prompt_cache=True, price_in=2.0, price_out=12.0,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="max",
    ),
    "gpt-5.6-luna": ModelInfo(
        id="gpt-5.6-luna", provider="openai", context_window=1_050_000,
        supports_prompt_cache=True, price_in=0.20, price_out=1.20,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="max",
    ),
    "gpt-5.5": ModelInfo(
        id="gpt-5.5", provider="openai", context_window=272_000,
        supports_prompt_cache=True, price_in=5.0, price_out=30.0,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="max",
    ),
    "gpt-5.4": ModelInfo(
        id="gpt-5.4", provider="openai", context_window=272_000,
        supports_prompt_cache=True, price_in=2.50, price_out=15.0,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="max",
    ),
    "gpt-5.4-mini": ModelInfo(
        id="gpt-5.4-mini", provider="openai", context_window=400_000,
        supports_prompt_cache=True, price_in=0.75, price_out=4.50,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="high",
    ),
    "gpt-5.4-nano": ModelInfo(
        id="gpt-5.4-nano", provider="openai", context_window=400_000,
        supports_prompt_cache=True, price_in=0.20, price_out=1.25,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="high",
    ),
    "gpt-5.2": ModelInfo(
        id="gpt-5.2", provider="openai", context_window=400_000,
        supports_prompt_cache=True, price_in=1.75, price_out=14.0,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="max",
    ),
    "gpt-5.1": ModelInfo(
        id="gpt-5.1", provider="openai", context_window=400_000,
        supports_prompt_cache=True, price_in=1.25, price_out=10.0,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="max",
    ),
    "gpt-5": ModelInfo(
        id="gpt-5", provider="openai", context_window=400_000,
        supports_prompt_cache=True, price_in=1.25, price_out=10.0,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="max",
    ),
    "gpt-5-mini": ModelInfo(
        id="gpt-5-mini", provider="openai", context_window=400_000,
        supports_prompt_cache=True, price_in=0.25, price_out=2.0,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="high",
    ),
    "gpt-5-nano": ModelInfo(
        id="gpt-5-nano", provider="openai", context_window=400_000,
        supports_prompt_cache=True, price_in=0.05, price_out=0.40,
        supports_thinking=True, thinking_style="openai_effort", max_thinking_level="high",
    ),
    # The 4.x families are not reasoning models, and their cached input is
    # billed at 0.25x (4.1) and 0.50x (4o) rather than the 0.10x everything
    # newer uses.
    "gpt-4.1": ModelInfo(
        id="gpt-4.1", provider="openai", context_window=1_047_576,
        supports_prompt_cache=True, price_in=2.0, price_out=8.0,
        cache_read_multiplier=0.25,
    ),
    "gpt-4.1-mini": ModelInfo(
        id="gpt-4.1-mini", provider="openai", context_window=1_047_576,
        supports_prompt_cache=True, price_in=0.40, price_out=1.60,
        cache_read_multiplier=0.25,
    ),
    "gpt-4.1-nano": ModelInfo(
        id="gpt-4.1-nano", provider="openai", context_window=1_047_576,
        supports_prompt_cache=True, price_in=0.10, price_out=0.40,
        cache_read_multiplier=0.25,
    ),
    "gpt-4o": ModelInfo(
        id="gpt-4o", provider="openai", context_window=128_000,
        supports_prompt_cache=True, price_in=2.50, price_out=10.0,
        cache_read_multiplier=0.50,
    ),
    "gpt-4o-mini": ModelInfo(
        id="gpt-4o-mini", provider="openai", context_window=128_000,
        supports_prompt_cache=True, price_in=0.15, price_out=0.60,
        cache_read_multiplier=0.50,
    ),
    # ------------------------------------------------------------------- google
    # Checked 2026-09-11 against https://ai.google.dev/gemini-api/docs/pricing
    # and https://ai.google.dev/gemini-api/docs/thinking.
    #
    # Thinking is on by default on every Gemini 3.x and 2.5 model *except*
    # `gemini-2.5-flash-lite`, which ships with it off — that is the one row
    # here where `--thinking off` is honored literally.
    #
    # Google's `thinking_level` enum is LOW/MEDIUM/HIGH (a `minimal` level has
    # since been added, and per-model support varies — `gemini-3.1-pro-preview`
    # accepts only low and high). Every row ceilings at "high" and
    # `resolve_thinking_level` clamps anything above it, loudly; modelling the
    # per-model floor would buy nothing this loop uses.
    "gemini-3.1-pro-preview": ModelInfo(
        id="gemini-3.1-pro-preview", provider="google", context_window=1_000_000,
        supports_prompt_cache=True, price_in=2.0, price_out=12.0,
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
        thinking_off_supported=False,
    ),
    # 0.75/3.75 is promotional through 2026-12-31; it reverts to 1.50/7.50 on
    # 2027-01-01. Re-check this block then — the price will go wrong silently.
    "gemini-3.8-flash": ModelInfo(
        id="gemini-3.8-flash", provider="google", context_window=1_000_000,
        supports_prompt_cache=True, price_in=0.75, price_out=3.75,
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
        thinking_off_supported=False,
    ),
    "gemini-3.7-flash": ModelInfo(
        id="gemini-3.7-flash", provider="google", context_window=1_000_000,
        supports_prompt_cache=True, price_in=0.75, price_out=3.75,
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
        thinking_off_supported=False,
    ),
    "gemini-3.6-flash": ModelInfo(
        id="gemini-3.6-flash", provider="google", context_window=1_000_000,
        supports_prompt_cache=True, price_in=0.75, price_out=3.75,
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
        thinking_off_supported=False,
    ),
    "gemini-3.5-flash": ModelInfo(
        id="gemini-3.5-flash", provider="google", context_window=1_000_000,
        supports_prompt_cache=True, price_in=1.50, price_out=9.0,
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
        thinking_off_supported=False,
    ),
    "gemini-3.5-flash-lite": ModelInfo(
        id="gemini-3.5-flash-lite", provider="google", context_window=1_000_000,
        supports_prompt_cache=True, price_in=0.30, price_out=2.50,
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
        thinking_off_supported=False,
    ),
    "gemini-3.1-flash-lite": ModelInfo(
        id="gemini-3.1-flash-lite", provider="google", context_window=1_000_000,
        supports_prompt_cache=True, price_in=0.25, price_out=1.50,
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
        thinking_off_supported=False,
    ),
    "gemini-2.5-pro": ModelInfo(
        id="gemini-2.5-pro", provider="google", context_window=1_000_000,
        supports_prompt_cache=True, price_in=1.25, price_out=10.0,
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
        thinking_off_supported=False,
    ),
    "gemini-2.5-flash": ModelInfo(
        id="gemini-2.5-flash", provider="google", context_window=1_000_000,
        supports_prompt_cache=False, price_in=0.30, price_out=2.50,
        supports_thinking=True, thinking_style="google_budget", max_thinking_level="high",
        thinking_off_supported=False,
    ),
    "gemini-2.5-flash-lite": ModelInfo(
        id="gemini-2.5-flash-lite", provider="google", context_window=1_000_000,
        supports_prompt_cache=False, price_in=0.10, price_out=0.40,
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
    """Resolve a requested --thinking level against a model's real behavior (D20).

    Returns ``(resolved_level, was_clamped)``. Both clamps below are recorded in
    `run.started`/`run.json` as `thinking_clamped_from`, so a degradation is
    never silent (D20, D11's "loud, not silent" posture).

    Two directions:

    - **Down to "off".** A model with no thinking support clamps any non-"off"
      request to "off" rather than erroring — a run that asked for thinking on a
      model that can't do it still runs, it just doesn't get to think. A request
      above a model's real ceiling clamps to that ceiling the same way.
    - **Up off "off"** (amended 2026-09-11). Most current models think by
      default, and some cannot be stopped at all; sending no thinking
      configuration does not mean no thinking happens. For those,
      ``--thinking off`` used to produce a run that thought, billed for it, and
      recorded ``thinking_level="off"`` with no clamp. Now it resolves to
      `MIN_REAL_THINKING_LEVEL` — the cheapest level that is an actual request —
      and reports the clamp, so the spend is both minimized and visible.

      The alternative for Anthropic would be to send ``thinking: {"type":
      "disabled"}``, which Sonnet 5 accepts. It is not used: Anthropic documents
      that disabling thinking on this model tier makes tool-heavy agentic
      workloads write tool calls into visible text, where they never execute and
      then pollute the history — which is exactly this loop's shape. Their own
      guidance is to leave thinking on and lower the effort instead.
    """
    if requested == "off":
        if model.supports_thinking and not model.thinking_off_supported:
            return MIN_REAL_THINKING_LEVEL, True
        return "off", False
    if not model.supports_thinking:
        return "off", True
    if THINKING_LEVELS.index(requested) > THINKING_LEVELS.index(model.max_thinking_level):
        return model.max_thinking_level, True
    return requested, False
