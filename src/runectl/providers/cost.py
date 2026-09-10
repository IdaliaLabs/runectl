"""Cost ledger: every LLM call, including utility calls, lands here (D5, plan §3.4)."""

from __future__ import annotations

from dataclasses import dataclass, field

from runectl.providers.registry import ModelInfo

# D18 — how much of `price_in` a cached token costs. Provider-standard for
# Anthropic today and identical across every registered model, so they are
# constants here rather than per-model fields (see registry.py's docstring).
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


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
            + (cache_read_tokens / per_million) * model.price_in * CACHE_READ_MULTIPLIER
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
