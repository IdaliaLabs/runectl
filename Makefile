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

# The same seeded run, recorded as an actual GIF: `asciinema` captures the
# real terminal byte stream scripts/record_demo.sh produces (headless — replay
# mode plays itself, so there are no keystrokes to simulate), `agg` renders the
# cast to GIF, and `ffmpeg` makes an MP4 and the still screenshot from it.
# `brew install asciinema agg ffmpeg` if any is missing.
#
# One generous take is recorded and then cut down, because the README wants a
# five-second loop and the TUI needs ~1.4s of that to boot. scripts/trim_cast.py
# does the cutting on the cast, so the GIF is encoded exactly once.
#
# The screenshot is a frame of the hero rather than a separate capture: two
# capture paths is how the committed screenshot drifted a release behind the
# GIF it sits next to. It comes from the hero's own cast re-cut to stop before
# the TUI's "playback complete" toast, which otherwise covers the timeline's
# exit-code line; seeking the rendered GIF by timestamp does not work, because
# agg coalesces frames and the toast frame spans the moment worth capturing.
DEMO_COLS := 138
DEMO_ROWS := 30
DEMO_START := 1.4
DEMO_LEN := 5.0

demo-video: demo-seed
	@mkdir -p docs/demo
	@rm -f docs/demo/session.cast docs/demo/hero.cast
	RUNECTL_HOME=$(DEMO_HOME) DEMO_DELAY=0.16 DEMO_DURATION=12 \
	  asciinema rec --command "bash scripts/record_demo.sh" \
	  --window-size $(DEMO_COLS)x$(DEMO_ROWS) --output-format asciicast-v2 \
	  docs/demo/session.cast
	uv run python scripts/trim_cast.py \
	  docs/demo/session.cast docs/demo/hero.cast $(DEMO_START) $(DEMO_LEN)
	agg --theme dracula --font-size 16 --last-frame-duration 1 \
	  docs/demo/hero.cast docs/demo/runectl-demo.gif
	ffmpeg -y -i docs/demo/runectl-demo.gif -movflags faststart -pix_fmt yuv420p \
	  -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" docs/demo/runectl-demo.mp4
	@shot=$$(uv run python scripts/trim_cast.py --frame-before-toast docs/demo/hero.cast) && \
	  echo "screenshot from the first $$shot s of the hero" && \
	  uv run python scripts/trim_cast.py docs/demo/hero.cast docs/demo/shot.cast 0 $$shot
	agg --theme dracula --font-size 16 --last-frame-duration 0 \
	  docs/demo/shot.cast docs/demo/shot.gif
	ffmpeg -y -v error -i docs/demo/shot.gif -update 1 docs/demo/tui-screenshot.png
	@rm -f docs/demo/shot.cast docs/demo/shot.gif
	@rm -f docs/demo/hero.cast
	@ls -la docs/demo/

# The three checks CI runs (CONTRIBUTING.md), as one target for local use.
check:
	uv run mypy --strict src/
	uv run ruff check .
	uv run pytest

test:
	uv run pytest
