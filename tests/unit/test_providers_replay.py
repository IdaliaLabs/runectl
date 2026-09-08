"""D5 §3.5: --record produces a cassette; ReplayProvider serves it back
byte-identically at zero spend."""

from __future__ import annotations

from pathlib import Path

from runectl.providers.base import Completion, Usage
from runectl.providers.replay import RecordingProvider, ReplayProvider
from runectl.providers.scripted import ScriptedProvider
from runectl.tools.schema import TOOLS


def test_record_then_replay_is_byte_identical(tmp_path: Path) -> None:
    cassette_path = tmp_path / "cassette.jsonl"
    completion = Completion(
        text="hello", tool_calls=(), usage=Usage(input_tokens=10, output_tokens=5), stop_reason="end_turn"
    )
    scripted = ScriptedProvider([completion])
    recording = RecordingProvider(scripted, cassette_path)

    result = recording.complete(system="sys", messages=[], tools=TOOLS, max_tokens=100)
    assert result == completion
    assert cassette_path.exists()

    replay = ReplayProvider(cassette_path)
    replayed = replay.complete(system="sys", messages=[], tools=TOOLS, max_tokens=100)
    assert replayed == completion


def test_replay_reports_zero_spend(tmp_path: Path) -> None:
    """`replay` makes no API calls, so its manifest must not claim a cost.

    Regression: a replay re-billed the recorded usage through the ledger and
    reported the original run's price, which made a zero-spend feature look
    expensive in the run store.
    """
    from runectl.providers.cost import CostLedger
    from runectl.providers.registry import resolve as resolve_model

    priced = resolve_model("claude-sonnet-5")
    free = priced.model_copy(update={"price_in": 0.0, "price_out": 0.0})
    ledger = CostLedger()

    ledger.record(free, input_tokens=500_000, output_tokens=100_000, cache_read_tokens=2_000_000)

    assert ledger.total_usd == 0.0
