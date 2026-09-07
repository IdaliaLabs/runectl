# Providers, models, and keys

`runectl` is bring-your-own-key across three first-class providers, with **no default
provider and no default model**. `--model` is always required. Different models are
genuinely better at different CTF categories, so choosing one is the user's call, not a
default we quietly make ([`DECISIONS.md`](../DECISIONS.md) D5).

## The model registry

Provider and capability come from an explicit table in
`src/runectl/providers/registry.py` — never from sniffing a string prefix like `claude-`.
An unregistered model id is a hard error (exit 6), not a guess.

| Model id | Provider | Context | Tools | Prompt cache | $/1M in | $/1M out |
|---|---|---|---|---|---|---|
| `claude-opus-5` | anthropic | 200K | ✓ | ✓ | 15.00 | 75.00 |
| `claude-sonnet-5` | anthropic | 200K | ✓ | ✓ | 3.00 | 15.00 |
| `claude-haiku-4-5-20251001` | anthropic | 200K | ✓ | ✓ | 1.00 | 5.00 |
| `gpt-5` | openai | 272K | ✓ | ✓ | 5.00 | 15.00 |
| `gpt-5-mini` | openai | 272K | ✓ | ✓ | 0.50 | 1.50 |
| `gemini-2.5-pro` | google | 1M | ✓ | ✓ | 1.25 | 10.00 |
| `gemini-2.5-flash` | google | 1M | ✓ | ✗ | 0.30 | 2.50 |

> Pricing and availability snapshot: **2026-09-05**. Re-verify before relying on it for
> real spend — this repo's convention is that every claim carries the date it was checked,
> because pricing rots fast.

### Adding a model

Add a `ModelInfo` entry to `MODEL_REGISTRY`. Nothing else changes — the cost ledger,
prompt caching, and utility-model selection all read from that row.

### Adding a provider

A fourth provider is a `ProviderName` literal, a registry entry, and one adapter module
implementing the `Provider` protocol. It is not a redesign.

## Key resolution

Precedence, highest first:

1. `--api-key <value>`
2. The environment variable — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`
3. The OS keyring (service name `runectl`, username the provider name)
4. `~/.config/runectl/keys.json`, mode 0600

```bash
uv run runectl keys set anthropic sk-ant-...   # keyring, or the 0600 file as fallback
uv run runectl keys list                       # presence only, never values
uv run runectl keys rm anthropic               # removes from both
```

If no key is found, `runectl run` exits **6** with a message naming all four options.

Keys are never written into a run's config snapshot, never logged, and are redacted from
the trace by a writer-level filter before anything hits disk — see
[`TRACE.md`](TRACE.md#redaction).

## The single call path

Every LLM call — the main loop's and internal utility calls alike — goes through
`providers.base.complete_with_retry`. That function is where retries, cost accounting, and
the transient/fatal error distinction live, so nothing can bypass them.

- **Non-streaming.** There is no live UI to feed, and non-streaming makes retries,
  caching, cassettes, and replay determinism straightforward. Live watchability comes from
  per-step trace events and the container's `/ctf/.agent_live.log`.
- **Retries.** 4 attempts by default, exponential backoff (`2^n`) plus up to 1s of jitter,
  on 429 / 5xx / connection / timeout errors. Each adapter maps its SDK's exception types
  onto `TransientProviderError`; anything else propagates immediately. Exhausting the
  attempts raises `ProviderError` → exit **5**.
- **Prompt caching** is applied to the stable system prefix where the registry says the
  model supports it, so category playbooks aren't re-billed every step.

## The utility model

Internal summarization (oversized tool output, history compaction) uses a separate,
cheaper model — but through the same path, into the same ledger.

- Default: the cheapest registered model of **the same provider** as `--model`, by
  `price_in + price_out`.
- Override with `--utility-model <id>`. If it belongs to a different provider, that
  provider's key is resolved independently.

There is no hardcoded utility model anywhere, and no silent no-op when a provider is
missing. Utility tokens show up in `cost.updated` events and in the run's total like any
other.

## Cost accounting

`CostLedger.record()` computes
`(input_tokens / 1e6) * price_in + (output_tokens / 1e6) * price_out` per call, keeps the
per-call entries, and maintains a running total. The loop emits a `cost.updated` event
after every call carrying both the call's cost and the cumulative total, and the final
figure lands in `run.json`.

## Record and replay

```bash
uv run runectl run --model gpt-5 --challenge chal.toml --record
uv run runectl replay <run_id> --check
```

`RecordingProvider` wraps a real provider and appends `{request_hash, response}` to
`cassette.jsonl` for every call. `ReplayProvider` reads that file and serves the recorded
completion back for a matching hash, with no network call.

The hash covers the system prompt, the full message list, the tool names, and
`max_tokens`. If the loop asks something the cassette doesn't contain, the replay raises
rather than silently improvising — that's the signal that your change altered the agent's
behavior.

`ScriptedProvider` is the test-only sibling: a fixed list of hand-written `Completion`
objects returned in order. Together with `StubSandbox`, it's why the whole test suite runs
with no daemon and no spend.

## Writing an adapter

Implement one method:

```python
class Provider(Protocol):
    def complete(
        self, *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> Completion: ...
```

`Message` is the canonical shape (`role`, `content`, `tool_call_id`, `tool_name`,
`tool_calls`); each adapter derives its own wire format at the boundary. Tool schemas are
derived from the single definition in `tools/schema.py` via `to_anthropic()`,
`to_openai()`, or `to_google()` — never hand-written per provider.

Map your SDK's rate-limit, connection, timeout, and 5xx exceptions onto
`TransientProviderError` so `complete_with_retry` can back off; let everything else
propagate.

Return a `Completion` with `text`, `tool_calls`, `usage`, and `stop_reason`.

> **Not yet verified against live APIs.** The three shipped adapters were written and
> type-checked against their installed SDKs but have never been exercised against the real
> services — no keys were available when they were built. Treat the first real run as the
> smoke test. See [`STATUS.md`](STATUS.md).
