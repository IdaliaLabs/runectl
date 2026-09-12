"""Cached and thinking tokens must reach the ledger (D18).

Regression for two bugs found 2026-09-11, both silent, both in the numbers this
tool publishes:

- OpenAI's adapter assigned `prompt_tokens` — the *total*, cached tokens
  included — straight to `Usage.input_tokens`, whose contract is uncached input
  only. Every cache hit was billed at the full input rate.
- Google's adapter did the same, and additionally dropped `thoughts_token_count`
  entirely. Gemini reports thinking tokens outside `candidates_token_count` and
  still bills them at the output rate, so reasoning spend was invisible — the
  understatement growing with exactly the setting that makes a run expensive.

Anthropic's adapter was already correct and is included here so the three stay
in agreement.
"""

from __future__ import annotations

from types import SimpleNamespace

from runectl.providers.cost import CostLedger
from runectl.providers.google import _usage as google_usage
from runectl.providers.openai import _usage as openai_usage
from runectl.providers.registry import resolve


def test_openai_cached_tokens_leave_input_tokens() -> None:
    usage = openai_usage(
        SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=800),
        )
    )
    assert usage.input_tokens == 200  # not 1000
    assert usage.cache_read_tokens == 800
    assert usage.output_tokens == 50
    assert usage.cache_write_tokens == 0  # OpenAI does not bill cache writes


def test_openai_usage_without_cache_details_is_all_uncached() -> None:
    usage = openai_usage(
        SimpleNamespace(prompt_tokens=300, completion_tokens=20, prompt_tokens_details=None)
    )
    assert (usage.input_tokens, usage.cache_read_tokens) == (300, 0)


def test_google_thinking_tokens_are_billed_as_output() -> None:
    usage = google_usage(
        SimpleNamespace(
            prompt_token_count=1000,
            candidates_token_count=50,
            thoughts_token_count=400,
            cached_content_token_count=800,
        )
    )
    assert usage.input_tokens == 200
    assert usage.cache_read_tokens == 800
    assert usage.output_tokens == 450  # 50 answer + 400 thinking, not 50


def test_missing_usage_metadata_is_zero_not_a_crash() -> None:
    for usage in (openai_usage(None), google_usage(None)):
        assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens) == (0, 0, 0)


def test_cache_read_multiplier_is_per_model() -> None:
    """`gpt-4o` reads cached input at 0.50x, not the 0.10x the old module-level
    constant applied to every model. One million cached tokens at $2.50/1M:
    $1.25 at the real rate, $0.25 at the old one."""
    ledger = CostLedger()
    cost = ledger.record(
        resolve("gpt-4o"), input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
    )
    assert cost == 1.25

    cheap = CostLedger().record(
        resolve("gpt-5"), input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
    )
    assert cheap == 0.125  # 1.25 * 0.10
