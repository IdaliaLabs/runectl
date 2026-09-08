"""Arena image lifecycle: inspect / build / load / pull (D2, D17, plan §2.4).

The arena image is the one piece of setup a user must do before any run, and
until D17 the only remedy on a fresh machine was "run `runectl arena build`" —
a 15-40 minute network build, with no way to hand runectl an image you already
had. This module is the whole lifecycle instead:

- :func:`inspect` answers "is it here, and is it current?" without raising for
  the ordinary "not built yet" case.
- :func:`build` builds from ``arena/Dockerfile`` and stamps the fingerprint of
  the Dockerfile it built from onto the image as a label.
- :func:`load_archive` loads a ``docker save`` tarball — the offline / "point at
  the file" path, and how a prebuilt arena moves between machines.
- :func:`pull_image` pulls a prebuilt image from a registry and tags it.

Shelling out to the ``docker`` CLI for build/load/pull is deliberate: those are
long, streaming operations whose progress output belongs on the user's terminal.
The SDK is used only for cheap inspection.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import APIError, DockerException, ImageNotFound

from runectl.errors import SandboxError
from runectl.sandbox.docker import ARENA_IMAGE

_ARENA_DIR = Path(__file__).resolve().parents[3] / "arena"

# Stamped onto the image at build time so a later run can tell whether the image
# was built from the Dockerfile currently in the tree (D17 staleness check).
FINGERPRINT_LABEL = "dev.idalia.runectl.arena-fingerprint"

# The upstream base the Dockerfile builds on. Named here only so error messages
# and `arena ensure` can talk about it concretely.
BASE_IMAGE = "kalilinux/kali-rolling:latest"

_LOADED_IMAGE_RE = re.compile(r"Loaded image(?: ID)?:\s*(\S+)")


def arena_fingerprint(context_dir: Path | None = None) -> str:
    """A stable short hash of the arena build context.

    Only the Dockerfile is hashed: it is the entire build context today, and
    hashing it means "the recipe changed" is detectable without depending on
    anything Docker records about layers.
    """
    dockerfile = (context_dir or _ARENA_DIR) / "Dockerfile"
    try:
        content = dockerfile.read_bytes()
    except OSError as exc:
        raise SandboxError(f"cannot read the arena Dockerfile at {dockerfile}: {exc}") from exc
    return hashlib.sha256(content).hexdigest()[:16]


@dataclass(frozen=True)
class ArenaStatus:
    """What we know about the arena image on this machine."""

    tag: str
    image_id: str | None
    built_fingerprint: str | None
    expected_fingerprint: str

    @property
    def present(self) -> bool:
        return self.image_id is not None

    @property
    def stale(self) -> bool:
        """Present, but built from a different Dockerfile than the one in the tree.

        An image built before fingerprint stamping existed has no label; that is
        reported as unknown provenance, not as stale, so an existing working
        image doesn't start nagging after an update.
        """
        return self.present and self.built_fingerprint is not None and (
            self.built_fingerprint != self.expected_fingerprint
        )

    @property
    def provenance_unknown(self) -> bool:
        return self.present and self.built_fingerprint is None


def _client(client: docker.DockerClient | None = None) -> docker.DockerClient:
    try:
        return client or docker.from_env()
    except DockerException as exc:
        raise SandboxError(
            f"could not reach the Docker daemon: {exc}\n"
            "Is Docker Desktop running? Start it and try again."
        ) from exc


def inspect(
    *,
    tag: str = ARENA_IMAGE,
    context_dir: Path | None = None,
    client: docker.DockerClient | None = None,
) -> ArenaStatus:
    """Report the arena image's presence and provenance.

    "Not built yet" is a normal answer, not an error. Only an unreachable daemon
    raises — the two failures are different problems with different fixes.
    """
    expected = arena_fingerprint(context_dir)
    active = _client(client)
    try:
        image = active.images.get(tag)
    except ImageNotFound:
        return ArenaStatus(tag=tag, image_id=None, built_fingerprint=None, expected_fingerprint=expected)
    except (APIError, DockerException) as exc:
        raise SandboxError(f"could not reach the Docker daemon: {exc}") from exc
    labels = image.labels or {}
    return ArenaStatus(
        tag=tag,
        image_id=str(image.id),
        built_fingerprint=labels.get(FINGERPRINT_LABEL),
        expected_fingerprint=expected,
    )


def build(*, tag: str = ARENA_IMAGE, context_dir: Path | None = None) -> int:
    """Run `docker build`, streaming to the caller's terminal. Returns the exit code.

    Stamps the build-context fingerprint onto the image so :func:`inspect` can
    later tell whether the image matches the Dockerfile in the tree.
    """
    context = context_dir or _ARENA_DIR
    fingerprint = arena_fingerprint(context_dir)
    result = subprocess.run(
        [
            "docker", "build",
            "-t", tag,
            "--label", f"{FINGERPRINT_LABEL}={fingerprint}",
            str(context),
        ],
        check=False,
    )
    return result.returncode


def load_archive(archive: Path, *, tag: str = ARENA_IMAGE) -> None:
    """Load a `docker save` tarball, tagging it as the arena image if needed.

    This is the offline / air-gapped / "I already have it on a USB stick" path:
    on a machine that has the image, `docker save runectl/arena:kali -o arena.tar`
    produces exactly what this consumes.
    """
    if not archive.exists():
        raise SandboxError(f"no such image archive: {archive}")
    result = subprocess.run(
        ["docker", "load", "-i", str(archive)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SandboxError(f"docker load failed: {result.stderr.strip() or result.stdout.strip()}")

    loaded = _LOADED_IMAGE_RE.findall(result.stdout)
    if not loaded:
        raise SandboxError(f"docker load reported no images from {archive}")
    if tag in loaded:
        return
    # The archive carried a different tag (or none). Point our tag at it rather
    # than making the user work out the `docker tag` invocation themselves.
    source = loaded[0]
    retag = subprocess.run(["docker", "tag", source, tag], check=False, capture_output=True, text=True)
    if retag.returncode != 0:
        raise SandboxError(
            f"loaded {source} from {archive}, but tagging it as {tag} failed: {retag.stderr.strip()}"
        )


def pull_image(reference: str, *, tag: str = ARENA_IMAGE) -> None:
    """Pull a prebuilt image from a registry and tag it as the arena image."""
    result = subprocess.run(["docker", "pull", reference], check=False)
    if result.returncode != 0:
        raise SandboxError(f"docker pull {reference} failed with exit code {result.returncode}")
    if reference == tag:
        return
    retag = subprocess.run(["docker", "tag", reference, tag], check=False, capture_output=True, text=True)
    if retag.returncode != 0:
        raise SandboxError(f"pulled {reference}, but tagging it as {tag} failed: {retag.stderr.strip()}")


def status(*, tag: str = ARENA_IMAGE, client: docker.DockerClient | None = None) -> str | None:
    """Return the image id if the arena image is built, else None.

    Kept as the thin presence check `DockerSandbox` and older callers use;
    `inspect` is the richer answer.
    """
    return inspect(tag=tag, client=client).image_id


REMEDY_MESSAGE = (
    "The arena sandbox image is not built yet, so there is nothing to run challenges in.\n"
    "\n"
    "Fix it with any one of these:\n"
    "  runectl arena ensure                      interactive setup (recommended first time)\n"
    "  runectl arena build                       build it here (~15-40 min, pulls Kali)\n"
    "  runectl arena ensure --from-file PATH     load a `docker save` tarball you already have\n"
    "  runectl arena ensure --from-registry REF  pull a prebuilt image and tag it\n"
)
