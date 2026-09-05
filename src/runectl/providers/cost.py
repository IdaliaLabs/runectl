"""Cost ledger: every LLM call, including utility calls, lands here (D5, plan §3.4)."""

from __future__ import annotations

from dataclasses import dataclass, field

from runectl.providers.registry import ModelInfo


@dataclass
class CostEntry:
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class CostLedger:
    entries: list[CostEntry] = field(default_factory=list)
    _total_usd: float = 0.0

    def record(self, model: ModelInfo, *, input_tokens: int, output_tokens: int) -> float:
        cost = (input_tokens / 1_000_000) * model.price_in + (output_tokens / 1_000_000) * model.price_out
        self.entries.append(
            CostEntry(
                model_id=model.id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            )
        )
        self._total_usd += cost
        return cost

    @property
    def total_usd(self) -> float:
        return self._total_usd
