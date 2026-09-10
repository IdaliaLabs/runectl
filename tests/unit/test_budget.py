"""D18/D19: cache-aware cost accounting, and the hard spend ceiling.

These matter because the tool is used against a prepaid balance — a ledger that
misprices cached tokens or a loop with no ceiling both spend real money.
"""

from __future__ import annotations

from runectl.providers.cost import CostLedger
from runectl.providers.registry import resolve as resolve_model


def test_cache_reads_are_a_tenth_of_fresh_input() -> None:
    model = resolve_model("claude-haiku-4-5")  # $1.00/1M in
    ledger = CostLedger()

    fresh = ledger.record(model, input_tokens=1_000_000, output_tokens=0)
    cached = ledger.record(model, input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000)

    assert fresh == 1.0
    assert cached == 0.10
    assert ledger.total_usd == 1.10


def test_cache_writes_cost_a_quarter_more() -> None:
    model = resolve_model("claude-haiku-4-5")
    ledger = CostLedger()

    assert ledger.record(model, input_tokens=0, output_tokens=0, cache_write_tokens=1_000_000) == 1.25


def test_registry_pricing_matches_the_published_rates() -> None:
    """Guards the 2026-09-07 correction: these were 3x too high for Opus."""
    opus = resolve_model("claude-opus-5")
    sonnet = resolve_model("claude-sonnet-5")

    assert (opus.price_in, opus.price_out) == (5.0, 25.0)
    assert (sonnet.price_in, sonnet.price_out) == (2.0, 10.0)
    assert opus.context_window == 1_000_000


def test_cheapest_utility_model_for_anthropic_is_haiku() -> None:
    from runectl.providers.registry import cheapest_model_for

    assert cheapest_model_for("anthropic").id == "claude-haiku-4-5"
