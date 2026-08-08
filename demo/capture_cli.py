#!/usr/bin/env python3
"""Record a scripted terminal session to an asciicast v2 file.

Reads a "tape" YAML describing shell commands with captions, runs each command
for real inside a pty, and writes:

- ``<out>.cast``      — asciicast v2 events with simulated typing and
                        time-compressed output (per-step ``timescale`` divides
                        output gaps, ``idle_limit`` clamps them), so the replay
                        is faithful but watchable.
- ``<out>.captions.json`` — ``[{"time": <cast seconds>, "text": ...}]`` cues,
                        one per step, consumed by ``terminal/player.html``.

Usage: python capture_cli.py <tape.yaml> <output-basename>
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import yaml

PROMPT = "\x1b[1;36m$\x1b[0m "
TYPE_DELAY = 0.045


class CastWriter:
    """Accumulates asciicast v2 events on a virtual clock."""

    def __init__(self, width: int, height: int) -> None:
        self.header = {
            "version": 2,
            "width": width,
            "height": height,
            "timestamp": int(time.time()),
        }
        self.events: list[tuple[float, str]] = []
        self.clock = 0.0

    def emit(self, data: str, *, advance: float = 0.0) -> None:
        self.clock += advance
        self.events.append((round(self.clock, 4), data))

    def wait(self, seconds: float) -> None:
        self.clock += seconds

    def write(self, path: Path) -> None:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.header) + "\n")
            for when, data in self.events:
                handle.write(json.dumps([when, "o", data]) + "\n")


def run_command(cast: CastWriter, command: str, step: dict) -> None:
    """Execute a command in a pty, appending its output on the virtual clock."""
    timescale = float(step.get("timescale", 1.0))
    idle_limit = float(step.get("idle_limit", 1.0))
    cwd = step.get("cwd") or os.getcwd()
    master, slave = pty.openpty()
    # Set the real pty window size: programs that ioctl the terminal (rather
    # than reading COLUMNS/LINES) must wrap for the cast's width, not 80x24.
    winsize = struct.pack("HHHH", cast.header["height"], cast.header["width"], 0, 0)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, winsize)
    env = {
        **os.environ,
        "TERM": "xterm-256color",
        "COLUMNS": str(cast.header["width"]),
        "LINES": str(cast.header["height"]),
    }
    process = subprocess.Popen(
        ["bash", "-lc", command],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=cwd,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    last = time.monotonic()
    while True:
        ready, _, _ = select.select([master], [], [], 0.25)
        if ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            now = time.monotonic()
            gap = min((now - last) / timescale, idle_limit)
            last = now
            cast.emit(chunk.decode("utf-8", "replace"), advance=gap)
        elif process.poll() is not None:
            break
    os.close(master)
    process.wait()


def main() -> int:
    tape_path, out_base = Path(sys.argv[1]), Path(sys.argv[2])
    tape = yaml.safe_load(tape_path.read_text(encoding="utf-8"))
    cast = CastWriter(int(tape.get("cols", 100)), int(tape.get("rows", 28)))
    captions: list[dict] = []

    for step in tape["steps"]:
        caption = step.get("caption")
        if caption:
            captions.append({"time": round(cast.clock, 4), "text": caption})
        cast.wait(float(step.get("prompt_delay", 0.9)))
        command = step.get("cmd")
        if not command:  # caption-only beat
            cast.wait(float(step.get("hold", 2.0)))
            continue
        shown = step.get("display", command)
        cast.emit(PROMPT)
        for char in shown:
            cast.emit(char, advance=TYPE_DELAY)
        cast.emit("\r\n", advance=0.35)
        run_command(cast, command, step)
        cast.wait(float(step.get("hold", 1.2)))

    cast.wait(float(tape.get("outro_hold", 2.5)))
    # Explicit end-of-tape marker: caption-only beats emit no output, and the
    # player replays until the LAST event — without this the trailing captions
    # would be cut off.
    cast.emit("")
    cast.write(out_base.with_suffix(".cast"))
    out_base.with_suffix(".captions.json").write_text(
        json.dumps(captions, indent=2), encoding="utf-8"
    )
    print(f"wrote {out_base}.cast ({cast.clock:.1f}s, {len(cast.events)} events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
