"""Real Docker sandbox: one container per run (D2).

NOTE for reviewers: this module could not be exercised against a live Docker
daemon while building the skeleton (no daemon was available in the build
environment). It is implemented to spec and type-checks, but needs a live-Docker
smoke test before it is trusted — see the M4 handoff report.
"""

from __future__ import annotations

import contextlib
import io
import shlex
import tarfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.models.containers import Container

from runectl.config import (
    CONTAINER_NAME_PREFIX,
    SANDBOX_CPUS,
    SANDBOX_ENV,
    SANDBOX_LIVE_LOG_PATH,
    SANDBOX_MEM_LIMIT,
    SANDBOX_PIDS_LIMIT,
    SANDBOX_PLATFORM,
    SANDBOX_WORKDIR,
)
from runectl.errors import SandboxError
from runectl.sandbox.base import ExecResult

ARENA_IMAGE = "runectl/arena:kali"


class DockerSandbox:
    """Implements the :class:`~runectl.sandbox.base.Sandbox` protocol structurally."""

    def __init__(
        self,
        *,
        image: str = ARENA_IMAGE,
        network: str = "bridge",
        run_id: str | None = None,
        platform: str = SANDBOX_PLATFORM,
        client: docker.DockerClient | None = None,
    ) -> None:
        self._image = image
        self._network = network
        self._platform = platform
        # Named after the run so a human can attach to it mid-run — the
        # predecessor's `docker exec -it ctf-agent-<id> bash` workflow, which was
        # actually used at competitions to take over from the agent.
        self._name = f"{CONTAINER_NAME_PREFIX}{run_id}" if run_id else None
        self._client_override = client
        self._client: docker.DockerClient | None = None
        self._container: Container | None = None

    @property
    def container_name(self) -> str | None:
        return self._name

    def start(self) -> None:
        # Deferred to start() rather than __init__: constructing a Sandbox must
        # never itself touch the daemon, so daemon-unreachable is always a
        # SandboxError a caller's try/except around start()/run() can catch —
        # never a raw SDK exception from plain object construction.
        try:
            self._client = self._client_override or docker.from_env()
            self._client.images.get(self._image)
        except ImageNotFound as exc:
            raise SandboxError(
                f"arena image {self._image!r} not found — run `runectl arena build` first"
            ) from exc
        except (APIError, DockerException) as exc:
            raise SandboxError(f"could not reach the Docker daemon: {exc}") from exc
        self._remove_stale_namesake()
        try:
            self._container = self._client.containers.run(
                self._image,
                name=self._name,
                command="sleep infinity",
                detach=True,
                platform=self._platform,
                mem_limit=SANDBOX_MEM_LIMIT,
                nano_cpus=int(SANDBOX_CPUS * 1_000_000_000),
                security_opt=["no-new-privileges"],
                privileged=False,
                pids_limit=SANDBOX_PIDS_LIMIT,
                environment=dict(SANDBOX_ENV),
                network_mode=self._network,
                working_dir=SANDBOX_WORKDIR,
            )
        except APIError as exc:
            raise SandboxError(f"failed to start arena container: {exc}") from exc
        setup = self._raw_exec(f"mkdir -p {SANDBOX_WORKDIR} && touch {SANDBOX_LIVE_LOG_PATH}", timeout_s=10)
        if not setup.ok:
            raise SandboxError(f"arena container failed workdir setup: {setup.stderr}")
        self._verify_non_root()

    def _verify_non_root(self) -> None:
        """Refuse to run the agent as root inside the sandbox (D2 posture).

        The posture used to rest entirely on the Dockerfile's `USER ctf`, which
        stopped being a guarantee once `arena ensure --from-file` and
        `--from-registry` let an arbitrary image become the arena. Root inside
        the container plus a kernel escape is a materially worse position than
        an unprivileged user, so this is checked rather than assumed.
        """
        probe = self._raw_exec("id -u", timeout_s=10)
        if not probe.ok:
            raise SandboxError(f"could not determine the sandbox user: {probe.stderr}")
        if probe.stdout.strip() == "0":
            raise SandboxError(
                f"the arena image {self._image!r} runs as root. runectl executes "
                "model-authored code in this container and will not do so as root. "
                "Add a non-root USER to the image (see arena/Dockerfile) and rebuild."
            )

    def _remove_stale_namesake(self) -> None:
        """Clear a leftover container of the same name (a previous crashed run).

        Named containers make attaching possible, but they also make a name
        collision possible; the predecessor removed the namesake before starting
        for exactly this reason.
        """
        if self._name is None or self._client is None:
            return
        try:
            existing = self._client.containers.get(self._name)
        except NotFound:
            return
        except (APIError, DockerException):
            return
        with contextlib.suppress(APIError):
            existing.remove(force=True)

    def _require_container(self) -> Container:
        if self._container is None:
            raise SandboxError("DockerSandbox: exec/write/read called before start()")
        return self._container

    def _raw_exec(self, script: str, *, timeout_s: int) -> ExecResult:
        """Unwrapped exec, no live-log tee — used for internal setup/verification only."""
        container = self._require_container()
        wrapped = f"timeout {timeout_s} bash -lc {shlex.quote(script)}"
        start = time.monotonic()
        try:
            result = container.exec_run(["bash", "-lc", wrapped], workdir=SANDBOX_WORKDIR, demux=True)
        except APIError as exc:
            raise SandboxError(f"exec failed: {exc}") from exc
        duration = time.monotonic() - start
        # The installed stub doesn't model demux=True's (bytes|None, bytes|None) return;
        # this is the documented shape for that mode (docker-py's exec_run docstring).
        output = cast("tuple[bytes | None, bytes | None]", result.output)
        stdout_raw, stderr_raw = output if output else (None, None)
        exit_code = result.exit_code
        return ExecResult(
            ok=exit_code == 0,
            stdout=(stdout_raw or b"").decode("utf-8", "replace"),
            stderr=(stderr_raw or b"").decode("utf-8", "replace"),
            exit_code=exit_code,
            duration_s=duration,
        )

    def exec(self, argv_or_script: str, *, timeout_s: int) -> ExecResult:
        """Mirrors combined output to the live log via process substitution (D2 item 12)
        while still returning stdout/stderr separately for the structured result."""
        inner = (
            f"{{ {argv_or_script}\n }} "
            f"> >(tee -a {SANDBOX_LIVE_LOG_PATH}) "
            f"2> >(tee -a {SANDBOX_LIVE_LOG_PATH} >&2)"
        )
        wrapped = f"timeout {timeout_s} bash -c {shlex.quote(inner)}"
        container = self._require_container()
        start = time.monotonic()
        try:
            result = container.exec_run(["bash", "-lc", wrapped], workdir=SANDBOX_WORKDIR, demux=True)
        except APIError as exc:
            raise SandboxError(f"exec failed: {exc}") from exc
        duration = time.monotonic() - start
        # The installed stub doesn't model demux=True's (bytes|None, bytes|None) return;
        # this is the documented shape for that mode (docker-py's exec_run docstring).
        output = cast("tuple[bytes | None, bytes | None]", result.output)
        stdout_raw, stderr_raw = output if output else (None, None)
        exit_code = result.exit_code
        return ExecResult(
            ok=exit_code == 0,
            stdout=(stdout_raw or b"").decode("utf-8", "replace"),
            stderr=(stderr_raw or b"").decode("utf-8", "replace"),
            exit_code=exit_code,
            duration_s=duration,
        )

    def write_file(self, rel_path: str, content: bytes) -> None:
        container = self._require_container()
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            info = tarfile.TarInfo(name=rel_path)
            info.size = len(content)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(content))
        if not container.put_archive(SANDBOX_WORKDIR, tar_buf.getvalue()):
            raise SandboxError(f"failed to write file into sandbox: {rel_path}")

    def put_inputs(self, files: Sequence[Path]) -> list[str]:
        container = self._require_container()
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            for file in files:
                tar.add(file, arcname=file.name)
        if not container.put_archive(SANDBOX_WORKDIR, tar_buf.getvalue()):
            raise SandboxError("failed to upload input files into sandbox")
        landed: list[str] = []
        for file in files:
            in_path = f"{SANDBOX_WORKDIR}/{file.name}"
            check = self._raw_exec(f"test -e {shlex.quote(in_path)}", timeout_s=10)
            if not check.ok:
                raise SandboxError(f"input file did not land in sandbox: {file.name}")
            landed.append(in_path)
        return landed

    def stop(self) -> None:
        if self._container is not None:
            try:
                self._container.remove(force=True)
            finally:
                self._container = None
