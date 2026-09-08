"""`runectl arena status|build|ensure` (D2, D17, plan §9.1).

`ensure` is the only interactive surface in the product, and it is interactive
only when a human is actually sitting at a TTY with no flags given. Every path
through it has a non-interactive form, and `runectl run` never routes here —
a run fails fast with instructions instead of prompting (D4).
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from runectl.errors import SandboxError
from runectl.sandbox import arena_build
from runectl.sandbox.docker import ARENA_IMAGE

app = typer.Typer(add_completion=False, help="Manage the arena sandbox image.")


def _report(status: arena_build.ArenaStatus) -> None:
    typer.echo(f"{status.tag} -> {status.image_id}")
    built = status.built_fingerprint or "unknown (built before provenance stamping)"
    typer.echo(f"  built from Dockerfile fingerprint: {built}")
    typer.echo(f"  Dockerfile in this tree:           {status.expected_fingerprint}")
    typer.echo(f"  architecture:                      {status.architecture or 'unknown'}")


@app.command("status")
def status_cmd() -> None:
    """Report whether the arena image exists and whether it is current."""
    try:
        current = arena_build.inspect()
    except SandboxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.exit_code) from exc

    if not current.present:
        typer.echo(f"arena image {current.tag} is not built", err=True)
        typer.echo(arena_build.REMEDY_MESSAGE, err=True)
        raise typer.Exit(code=4)

    _report(current)
    if current.architecture_mismatch:
        typer.echo(
            "  "
            + arena_build.ARCH_WARNING.format(
                tag=current.tag,
                actual=current.architecture,
                expected=current.expected_architecture,
            ),
            err=True,
        )
    if current.stale:
        typer.echo(
            "  STALE: the Dockerfile changed since this image was built. "
            "Run `runectl arena build` to pick up the new toolset.",
            err=True,
        )


@app.command("build")
def build_cmd() -> None:
    """Build the arena image from arena/Dockerfile."""
    code = arena_build.build()
    raise typer.Exit(code=code)


@app.command("ensure")
def ensure_cmd(
    from_file: str | None = typer.Option(
        None, "--from-file", help="Load a `docker save` tarball instead of building"
    ),
    from_registry: str | None = typer.Option(
        None, "--from-registry", help="Pull a prebuilt image reference and tag it as the arena image"
    ),
    build: bool = typer.Option(False, "--build", help="Build from arena/Dockerfile"),
    force: bool = typer.Option(False, "--force", help="Act even if a current image is already present"),
) -> None:
    """Make sure the arena image exists, offering every way of getting one.

    With no flags on a TTY this asks which route you want. With no flags and no
    TTY it reports what's missing and exits 4 — it never blocks a script.
    """
    try:
        current = arena_build.inspect()
    except SandboxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.exit_code) from exc

    if current.present and not force and not (from_file or from_registry or build):
        _report(current)
        if current.stale:
            typer.echo(
                "  STALE: built from a different Dockerfile than the one in this tree.\n"
                "  Re-run with `runectl arena ensure --build --force` to rebuild.",
                err=True,
            )
        else:
            typer.echo("arena image is present and current — nothing to do")
        return

    chosen_file, chosen_registry, chosen_build = from_file, from_registry, build
    if not (chosen_file or chosen_registry or chosen_build):
        if not sys.stdin.isatty():
            typer.echo(f"arena image {current.tag} is not built", err=True)
            typer.echo(arena_build.REMEDY_MESSAGE, err=True)
            raise typer.Exit(code=4)
        chosen_file, chosen_registry, chosen_build = _ask()

    try:
        if chosen_file:
            typer.echo(f"loading {chosen_file} ...", err=True)
            arena_build.load_archive(Path(chosen_file).expanduser())
        elif chosen_registry:
            typer.echo(f"pulling {chosen_registry} ...", err=True)
            arena_build.pull_image(chosen_registry)
        else:
            code = arena_build.build()
            if code != 0:
                typer.echo(f"docker build failed with exit code {code}", err=True)
                raise typer.Exit(code=4)
    except SandboxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.exit_code) from exc

    final = arena_build.inspect()
    if not final.present:
        typer.echo(f"finished, but {final.tag} still isn't present — nothing was installed", err=True)
        raise typer.Exit(code=4)
    _report(final)
    typer.echo("arena image ready — `runectl run` will work now")


def _ask() -> tuple[str | None, str | None, bool]:
    """Prompt a human for the setup route. Only ever called on a TTY."""
    typer.echo(f"The arena sandbox image ({ARENA_IMAGE}) isn't on this machine yet.", err=True)
    typer.echo("Challenges run inside it, so runectl needs one before it can do anything.\n", err=True)
    typer.echo("  1) Build it here from arena/Dockerfile", err=True)
    typer.echo(f"     ~15-40 min, several GB, pulls {arena_build.BASE_IMAGE} from Docker Hub", err=True)
    typer.echo("  2) Load an image file I already have (a `docker save` tarball)", err=True)
    typer.echo("  3) Pull a prebuilt image from a registry", err=True)
    typer.echo("  4) Cancel\n", err=True)

    choice = typer.prompt("Which", default="1").strip()
    if choice == "2":
        return typer.prompt("Path to the image tarball").strip(), None, False
    if choice == "3":
        return None, typer.prompt("Image reference to pull").strip(), False
    if choice == "4":
        raise typer.Exit(code=4)
    return None, None, True
