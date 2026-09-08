"""Regression: constructing a DockerSandbox must never itself touch the Docker
daemon — only start()/exec() may, and only ever raising SandboxError, never a
raw SDK exception, so a caller's try/except around start() always catches it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from docker.errors import DockerException, NotFound

from runectl.errors import SandboxError
from runectl.sandbox.docker import DockerSandbox


def test_construction_does_not_touch_daemon() -> None:
    # No client override given; if __init__ eagerly called docker.from_env()
    # (which fails fast when no daemon socket exists) this would already raise.
    DockerSandbox(network="bridge")


def test_start_wraps_daemon_failure_as_sandbox_error() -> None:
    """A dead daemon surfaces as SandboxError, never a raw SDK exception.

    Uses an explicit failing client rather than relying on the developer's
    machine having no daemon — this test silently started real containers once
    a daemon and an arena image existed locally.
    """

    class _DeadClient:
        images = SimpleNamespace(
            get=lambda tag: (_ for _ in ()).throw(DockerException("daemon is not running"))
        )

    sandbox = DockerSandbox(network="bridge", client=_DeadClient())
    with pytest.raises(SandboxError, match="could not reach the Docker daemon"):
        sandbox.start()


def test_root_arena_image_is_refused() -> None:
    """Model-authored code must not run as root, even if a loaded image says so."""
    captured: dict[str, Any] = {}

    class _Container:
        def exec_run(self, *args: Any, **kwargs: Any) -> Any:
            # workdir setup succeeds; the `id -u` probe reports root
            script = args[0][-1] if args else ""
            if "id -u" in script:
                return SimpleNamespace(exit_code=0, output=(b"0\n", b""))
            return SimpleNamespace(exit_code=0, output=(b"", b""))

    class _Client:
        images = SimpleNamespace(get=lambda tag: SimpleNamespace(id="sha256:abc"))
        containers = SimpleNamespace(
            get=lambda name: (_ for _ in ()).throw(NotFound(name)),
            run=lambda image, **kw: captured.update(kw) or _Container(),
        )

    sandbox = DockerSandbox(network="none", run_id="r", client=_Client())
    with pytest.raises(SandboxError, match="runs as root"):
        sandbox.start()


def test_pids_limit_is_applied() -> None:
    """A fork bomb in an agent-written exploit shouldn't threaten the host VM."""
    captured: dict[str, Any] = {}

    class _Container:
        def exec_run(self, *args: Any, **kwargs: Any) -> Any:
            script = args[0][-1] if args else ""
            if "id -u" in script:
                return SimpleNamespace(exit_code=0, output=(b"1000\n", b""))
            return SimpleNamespace(exit_code=0, output=(b"", b""))

    class _Client:
        images = SimpleNamespace(get=lambda tag: SimpleNamespace(id="sha256:abc"))
        containers = SimpleNamespace(
            get=lambda name: (_ for _ in ()).throw(NotFound(name)),
            run=lambda image, **kw: captured.update(kw) or _Container(),
        )

    DockerSandbox(network="none", run_id="r", client=_Client()).start()

    assert captured["pids_limit"] == 512


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
            script = args[0][-1] if args else ""
            if "id -u" in script:
                return SimpleNamespace(exit_code=0, output=(b"1000\n", b""))
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
