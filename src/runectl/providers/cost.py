"""Cost ledger: every LLM call, including utility calls, lands here (D5, plan §3.4)."""

from __future__ import annotations

from dataclasses import dataclass, field

from runectl.providers.registry import ModelInfo

# D18 — how much of `price_in` a cache *write* costs. Still a constant: no
# registered model differs, and OpenAI and Google don't bill cache writes at all
# (their adapters report zero write tokens).
#
# The cache *read* multiplier used to live here too, at a flat 0.10. It moved to
# `ModelInfo.cache_read_multiplier` on 2026-09-11, when the registry grew rows
# that genuinely differ: OpenAI reads `gpt-4.1*` at 0.25x and `gpt-4o*` at 0.50x
# of `price_in`, not 0.10x. Pricing those at the old constant understated the
# cached portion of a run by up to 5x. registry.py's docstring had reserved
# exactly this move ("make them fields again the day a provider actually
# differs").
CACHE_WRITE_MULTIPLIER = 1.25


@dataclass
class CostEntry:
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class CostLedger:
    """Every LLM call lands here, priced with cache tiers (D5, D18).

    A cache read costs a tenth of a fresh input token and a cache write costs
    a quarter more, so a ledger that ignored them would misreport a cached
    agent loop badly — which matters when the user is working against a fixed
    prepaid balance.
    """

    entries: list[CostEntry] = field(default_factory=list)
    _total_usd: float = 0.0

    def record(
        self,
        model: ModelInfo,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        per_million = 1_000_000
        cost = (
            (input_tokens / per_million) * model.price_in
            + (cache_write_tokens / per_million) * model.price_in * CACHE_WRITE_MULTIPLIER
            + (cache_read_tokens / per_million) * model.price_in * model.cache_read_multiplier
            + (output_tokens / per_million) * model.price_out
        )
        self.entries.append(
            CostEntry(
                model_id=model.id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
            )
        )
        self._total_usd += cost
        return cost

    @property
    def total_usd(self) -> float:
        return self._total_usd
