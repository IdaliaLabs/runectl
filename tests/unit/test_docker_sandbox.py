"""Regression: constructing a DockerSandbox must never itself touch the Docker
daemon — only start()/exec() may, and only ever raising SandboxError, never a
raw SDK exception, so a caller's try/except around start() always catches it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from docker.errors import NotFound

from runectl.errors import SandboxError
from runectl.sandbox.docker import DockerSandbox


def test_construction_does_not_touch_daemon() -> None:
    # No client override given; if __init__ eagerly called docker.from_env()
    # (which fails fast when no daemon socket exists) this would already raise.
    DockerSandbox(network="bridge")


def test_start_wraps_daemon_failure_as_sandbox_error() -> None:
    sandbox = DockerSandbox(network="bridge")
    with pytest.raises(SandboxError):
        sandbox.start()


def test_container_is_named_after_the_run() -> None:
    """A predictable name is what makes `docker exec -it runectl-<run_id> bash`
    possible mid-run — the predecessor's take-over workflow."""
    sandbox = DockerSandbox(network="bridge", run_id="20260907-220636-9221b7")

    assert sandbox.container_name == "runectl-20260907-220636-9221b7"


def test_container_is_unnamed_without_a_run_id() -> None:
    assert DockerSandbox(network="bridge").container_name is None


def test_start_pins_the_platform_and_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """An arm64 arena cannot execute x86-64 challenge binaries, so the platform
    is pinned on the container as well as on the image."""
    captured: dict[str, Any] = {}

    class _Containers:
        def get(self, name: str) -> Any:
            raise NotFound(name)

        def run(self, image: str, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return _Container()

    class _Container:
        def exec_run(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(exit_code=0, output=(b"", b""))

    class _Client:
        images = SimpleNamespace(get=lambda tag: SimpleNamespace(id="sha256:abc"))
        containers = _Containers()

    sandbox = DockerSandbox(network="none", run_id="run-1", client=_Client())
    sandbox.start()

    assert captured["platform"] == "linux/amd64"
    assert captured["name"] == "runectl-run-1"
    assert captured["network_mode"] == "none"
    assert captured["privileged"] is False
    assert captured["security_opt"] == ["no-new-privileges"]
