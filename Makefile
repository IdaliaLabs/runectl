.PHONY: demo demo-seed demo-frames demo-video check test

DEMO_HOME := $(CURDIR)/.demo-home

# Shared by demo/demo-frames/demo-video below: one solved run built with
# StubSandbox + ScriptedProvider (scripts/seed_demo.py) — zero spend, no
# Docker daemon, no API key. Safe to run repeatedly: the scratch RUNECTL_HOME
# is wiped first, so it never touches a real run store.
demo-seed:
	@rm -rf $(DEMO_HOME)
	@mkdir -p $(DEMO_HOME)
	@RUNECTL_HOME=$(DEMO_HOME) uv run python scripts/seed_demo.py > $(DEMO_HOME)/.run_id
	@RUNECTL_HOME=$(DEMO_HOME) uv run runectl index rebuild >/dev/null

# The demo this project set out to build: "show the thought, show the
# command, show the output, show the next move". The TUI animates through the seeded run's
# already-recorded trace via `runectl tui --replay` (never `runectl replay`,
# which re-executes the loop and is not what a demo needs).
demo: demo-seed
	RUNECTL_HOME=$(DEMO_HOME) uv run runectl tui --replay "$$(cat $(DEMO_HOME)/.run_id)"

# Phase 5's demo as still frames, captured headlessly through Textual's test
# harness — no TTY, no recording tool, no spend. SVG because it is text: it
# diffs, it scales, and it keeps binaries out of the repo.
demo-frames: demo-seed
	RUNECTL_HOME=$(DEMO_HOME) uv run python scripts/capture_demo.py \
	  "$$(cat $(DEMO_HOME)/.run_id)" docs/demo

# The same seeded run, recorded as an actual video/GIF: `asciinema` captures
# the real terminal byte stream scripts/record_demo.sh produces (headless,
# no keystrokes to simulate — replay mode plays itself), `agg` renders the
# cast to GIF, and `ffmpeg` (already a dev dependency of nothing else here,
# just needs to be on PATH) makes an MP4 from that GIF for a size-limited
# README embed. `brew install asciinema agg` if either is missing.
demo-video: demo-seed
	@mkdir -p docs/demo
	@rm -f docs/demo/session.cast
	RUNECTL_HOME=$(DEMO_HOME) asciinema rec --command "bash scripts/record_demo.sh" \
	  --window-size 132x40 --output-format asciicast-v2 docs/demo/session.cast
	agg --theme dracula --font-size 16 \
	  docs/demo/session.cast docs/demo/runectl-demo.gif
	ffmpeg -y -i docs/demo/runectl-demo.gif -movflags faststart -pix_fmt yuv420p \
	  -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" docs/demo/runectl-demo.mp4

# The three checks CI runs (CONTRIBUTING.md), as one target for local use.
check:
	uv run mypy --strict src/
	uv run ruff check .
	uv run pytest

test:
	uv run pytest
