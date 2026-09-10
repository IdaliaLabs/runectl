"""User-editable preferences: ``~/.config/runectl/config.toml`` (Phase 2, D5/D20).

Distinct from ``config.py`` (fixed paths and unmeasured engine tunables) and
from ``providers/keys.py`` (secrets — the keyring or a 0600 file, never here).
This file stores *preferences* only: a default model and thinking level per
provider. It is plain text, non-secret, and safe to check into dotfiles.

This does **not** relax D5. ``runectl run`` still requires ``--model`` on
every invocation — nothing here is read by the run path to silently choose a
model. What reads this file is discovery/convenience surfaces only:
``runectl models list`` (to show what's configured) and the TUI's launcher (to
*prefill* a field the user still sees and can change before it composes an
explicit ``--model``/``--thinking`` command line).

Only ``model`` and ``thinking`` are read by anything. ``runectl config`` will
happily store other keys, and readers for ``approval``/``max_cost`` existed
here for a while without ever being wired into the run path — removed
2026-09-09 rather than left standing as an unkept promise. Setting those keys
today does nothing.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from runectl.config import runectl_config_dir
from runectl.errors import UsageError
from runectl.providers.registry import THINKING_LEVELS, ProviderName, ThinkingLevel

# The same "looks like a secret" shapes trace/writer.py redacts on the way out
# of the trace, reused here on the way in: a key pasted into config.toml by
# mistake is rejected at load time rather than sitting unprotected in a
# plaintext file no code path was written to guard (D5's "never written to
# project config" — this file *is* project/user config).
_KEY_SHAPED = re.compile(
    r"^(sk-ant-[A-Za-z0-9_-]{10,}|sk-proj-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{20,})$"
)


def config_path() -> Path:
    return runectl_config_dir() / "config.toml"


def _reject_key_shaped(section: str, key: str, value: str) -> None:
    if _KEY_SHAPED.match(value):
        raise UsageError(
            f"refusing to read/write {section}.{key} in {config_path()}: it looks like an "
            f"API key. config.toml is plain text, not the keyring — use "
            f"`runectl keys set {section}` instead."
        )


def load() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text())
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if isinstance(value, str):
                _reject_key_shaped(section, key, value)
    return data


def get(section: str, key: str) -> str | None:
    data = load()
    values = data.get(section)
    if not isinstance(values, dict):
        return None
    raw = values.get(key)
    return str(raw) if raw is not None else None


def set_value(section: str, key: str, value: str) -> None:
    _reject_key_shaped(section, key, value)
    data = load()
    section_data = data.get(section)
    if section_data is None:
        section_data = {}
        data[section] = section_data
    if not isinstance(section_data, dict):
        raise UsageError(f"{config_path()}: [{section}] is not a table")
    section_data[key] = value
    _write_raw(data)


def _write_raw(data: dict[str, Any]) -> None:
    # No TOML writer dependency (D1's runtime deps list is locked): the shape
    # this file ever needs to produce is flat string key/values under
    # `[section]` headers, so a hand-rolled serializer is simpler and more
    # honest than adding a dependency for it. `json.dumps` gives correctly
    # escaped TOML basic-string literals for the characters that matter here
    # (quotes, backslashes) without reimplementing that escaping by hand.
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for section in sorted(data):
        values = data[section]
        if not isinstance(values, dict) or not values:
            continue
        lines.append(f"[{section}]")
        for key in sorted(values):
            lines.append(f"{key} = {json.dumps(str(values[key]))}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def default_model(provider: ProviderName) -> str | None:
    return get(provider, "model")


def default_thinking(provider: ProviderName) -> ThinkingLevel:
    """The configured default thinking level for a provider, or "off".

    Raises `UsageError` on a stored value outside the shared vocabulary rather
    than silently falling back to "off" — a config file with a typo in it
    should not degrade quietly (D20's "loud, not silent" posture).
    """
    raw = get(provider, "thinking")
    if raw is None:
        return "off"
    if raw not in THINKING_LEVELS:
        raise UsageError(
            f"{config_path()}: [{provider}] thinking = {raw!r} is not one of "
            f"{', '.join(THINKING_LEVELS)}"
        )
    return raw
