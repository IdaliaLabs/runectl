"""Provider key resolution (D5, plan §3.3).

Precedence: ``--api-key`` > env > OS keyring > ``~/.config/runectl/keys.json``
(mode 0600). Keys are never written to project/run config and never logged;
the trace writer's redaction patterns (`trace/writer.py`) are the last line of
defense if one leaks into an event anyway.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from pathlib import Path

import keyring
import keyring.errors

from runectl.config import keys_file_path
from runectl.errors import UsageError
from runectl.providers.registry import ProviderName

_ENV_VAR: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}
_KEYRING_SERVICE = "runectl"


def _read_keys_file() -> dict[str, str]:
    path = keys_file_path()
    if not path.exists():
        return {}
    data: dict[str, str] = json.loads(path.read_text())
    return data


def _write_keys_file(keys: dict[str, str]) -> None:
    path = keys_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _try_keyring_get(provider: str) -> str | None:
    try:
        return keyring.get_password(_KEYRING_SERVICE, provider)
    except keyring.errors.KeyringError:
        return None


def resolve_key(provider: ProviderName, *, cli_flag: str | None = None) -> str:
    if cli_flag:
        return cli_flag
    env_value = os.environ.get(_ENV_VAR[provider])
    if env_value:
        return env_value
    keyring_value = _try_keyring_get(provider)
    if keyring_value:
        return keyring_value
    file_value = _read_keys_file().get(provider)
    if file_value:
        return file_value
    raise UsageError(
        f"no API key found for provider {provider!r}. Set --api-key, "
        f"${_ENV_VAR[provider]}, run `runectl keys set {provider}`, or add it "
        f"to {keys_file_path()}"
    )


def set_key(provider: ProviderName, api_key: str) -> None:
    try:
        keyring.set_password(_KEYRING_SERVICE, provider, api_key)
        return
    except keyring.errors.KeyringError:
        pass
    keys = _read_keys_file()
    keys[provider] = api_key
    _write_keys_file(keys)


def remove_key(provider: ProviderName) -> None:
    with contextlib.suppress(keyring.errors.KeyringError):
        keyring.delete_password(_KEYRING_SERVICE, provider)
    keys = _read_keys_file()
    if provider in keys:
        del keys[provider]
        _write_keys_file(keys)


def list_keys() -> dict[str, bool]:
    """Presence only — never returns key values."""
    keys = _read_keys_file()
    return {
        provider: bool(os.environ.get(env_var) or _try_keyring_get(provider) or keys.get(provider))
        for provider, env_var in _ENV_VAR.items()
    }


def keys_path() -> Path:
    return keys_file_path()
