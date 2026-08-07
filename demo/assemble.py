#!/usr/bin/env python3
"""Assemble a recorded demo video: title card + wait-compression + H.264 encode.

Usage:
    python assemble.py build/studio.webm docs/assets/videos/silisocs-studio-tour.mp4 \
        --title "Silisocs Studio" --subtitle "A four-minute tour" \
        [--segments build/studio.segments.json] [--wait-speed 12]

- The optional segments file (written by record_studio.mjs) lists
  ``[{"name", "start", "wait"}]`` marks; spans flagged ``wait: true`` are
  time-compressed by ``--wait-speed`` so LLM latency does not bloat the video.
- The title card is rendered with ffmpeg drawtext on the shared dark backdrop.
- Output: 1280x720 H.264 (CRF 27) + faststart, small enough to commit.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def font_file(bold: bool = False) -> str:
    pattern = "DejaVu Sans:style=Bold" if bold else "DejaVu Sans"
    out = subprocess.run(
        ["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def spans(marks: list[dict], duration: float) -> list[tuple[float, float, float]]:
    """(start, end, speed) spans covering the whole recording."""
    if not marks:
        return [(0.0, duration, 1.0)]
    result = []
    for index, mark in enumerate(marks):
        start = mark["start"]
        end = marks[index + 1]["start"] if index + 1 < len(marks) else duration
        if end - start < 0.05:
            continue
        result.append(
            (start, end, float(mark.get("speed", 0)) or (12.0 if mark.get("wait") else 1.0))
        )
    return result


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--title", required=True)
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--segments")
    parser.add_argument("--wait-speed", type=float, default=12.0)
    parser.add_argument("--card-seconds", type=float, default=2.8)
    args = parser.parse_args()

    source = Path(args.source)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(source)
    marks = json.loads(Path(args.segments).read_text()) if args.segments else []
    for mark in marks:
        if mark.get("wait"):
            mark["speed"] = args.wait_speed
    pieces = spans(marks, duration)

    filters = [
        # Title card: dark backdrop, title + subtitle, fade in/out.
        f"color=c=0x10141c:s=1280x720:d={args.card_seconds}:r=30,"
        f"drawtext=fontfile={font_file(bold=True)}:text='{escape(args.title)}':"
        "fontcolor=0xe8ecf4:fontsize=58:x=(w-text_w)/2:y=(h-text_h)/2-34,"
        f"drawtext=fontfile={font_file()}:text='{escape(args.subtitle)}':"
        "fontcolor=0x8b93a7:fontsize=27:x=(w-text_w)/2:y=(h)/2+34,"
        f"fade=t=in:d=0.4,fade=t=out:st={args.card_seconds - 0.45}:d=0.45,setsar=1[card]"
    ]
    for index, (start, end, speed) in enumerate(pieces):
        filters.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},"
            f"setpts=(PTS-STARTPTS)/{speed},fps=30,setsar=1[v{index}]"
        )
    chain = "[card]" + "".join(f"[v{i}]" for i in range(len(pieces)))
    filters.append(f"{chain}concat=n={len(pieces) + 1}:v=1:a=0[out]")

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[out]",
        "-c:v",
        "libx264",
        "-crf",
        "27",
        "-preset",
        "slow",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    subprocess.run(command, check=True)
    size = output.stat().st_size / 1e6
    print(f"wrote {output} ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
