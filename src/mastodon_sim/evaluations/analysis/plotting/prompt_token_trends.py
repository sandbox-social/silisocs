#!/usr/bin/env python3
"""Analyze prompt/response token usage by episode and phase.

Input: prompts_and_responses.jsonl
Output:
- episode_token_summary.csv
- episode_token_trends.png
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

_TOKEN_FALLBACK_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def _build_token_counter(encoding: str):
    if encoding == "word_estimate":
        return lambda text: len(_TOKEN_FALLBACK_RE.findall(text or ""))

    try:
        import tiktoken
    except Exception:
        return lambda text: len(_TOKEN_FALLBACK_RE.findall(text or ""))

    chosen_encoding = "cl100k_base" if encoding == "auto" else encoding
    enc = tiktoken.get_encoding(chosen_encoding)
    return lambda text: len(enc.encode(text or ""))


def _classify_phase(prompt: str, episode_idx: int) -> str:
    lower = (prompt or "").lower()
    if "you are completing a survey in character." in lower and "questions:" in lower:
        return "probe"
    if (
        "final decision:" in lower
        and "action type:" in lower
        and "target id:" in lower
    ):
        return "action"
    if "has to make their first post on social media" in lower:
        return "startup_seed"
    if episode_idx == -1:
        return "startup_other"
    return "other"


def _read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                yield line_no, json.loads(raw_line)
            except json.JSONDecodeError:
                continue


def _write_csv(rows: list[dict], csv_path: Path) -> None:
    fieldnames = [
        "episode",
        "phase",
        "calls",
        "avg_prompt_tokens",
        "avg_output_tokens",
        "avg_total_tokens",
        "sum_prompt_tokens",
        "sum_output_tokens",
        "sum_total_tokens",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _plot_rows(rows: list[dict], out_path: Path, phases: tuple[str, ...]) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    by_phase: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["phase"] in phases:
            by_phase[row["phase"]].append(row)

    if not by_phase:
        return

    for phase_rows in by_phase.values():
        phase_rows.sort(key=lambda x: x["episode"])

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    metrics = [
        ("avg_prompt_tokens", "Average Prompt Tokens"),
        ("avg_output_tokens", "Average Output Tokens"),
        ("avg_total_tokens", "Average Total Tokens"),
    ]

    for ax, (metric, title) in zip(axes, metrics, strict=False):
        for phase, phase_rows in sorted(by_phase.items()):
            x = [row["episode"] for row in phase_rows]
            y = [row[metric] for row in phase_rows]
            ax.plot(x, y, marker="o", linewidth=1.5, markersize=3, label=phase)
        ax.set_title(title)
        ax.set_ylabel("tokens")
        ax.grid(alpha=0.3)
        ax.legend()

    axes[-1].set_xlabel("episode")
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def analyze(input_path: Path, output_dir: Path, encoding: str, make_plot: bool) -> None:
    token_count = _build_token_counter(encoding)

    agg: dict[tuple[int, str], dict[str, float]] = defaultdict(
        lambda: {
            "calls": 0.0,
            "sum_prompt_tokens": 0.0,
            "sum_output_tokens": 0.0,
            "sum_total_tokens": 0.0,
        }
    )

    for _line_no, rec in _read_jsonl(input_path):
        prompt = rec.get("prompt", "") or ""
        output = rec.get("output", "") or ""
        episode_idx = int(rec.get("episode_idx", -1))
        phase = _classify_phase(prompt, episode_idx)

        p_tokens = float(token_count(prompt))
        o_tokens = float(token_count(output))
        key = (episode_idx, phase)
        agg[key]["calls"] += 1.0
        agg[key]["sum_prompt_tokens"] += p_tokens
        agg[key]["sum_output_tokens"] += o_tokens
        agg[key]["sum_total_tokens"] += (p_tokens + o_tokens)

    rows: list[dict] = []
    for (episode, phase), stats in sorted(agg.items(), key=lambda x: (x[0][0], x[0][1])):
        calls = stats["calls"] or 1.0
        rows.append(
            {
                "episode": episode,
                "phase": phase,
                "calls": int(stats["calls"]),
                "avg_prompt_tokens": round(stats["sum_prompt_tokens"] / calls, 3),
                "avg_output_tokens": round(stats["sum_output_tokens"] / calls, 3),
                "avg_total_tokens": round(stats["sum_total_tokens"] / calls, 3),
                "sum_prompt_tokens": int(stats["sum_prompt_tokens"]),
                "sum_output_tokens": int(stats["sum_output_tokens"]),
                "sum_total_tokens": int(stats["sum_total_tokens"]),
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "episode_token_summary.csv"
    _write_csv(rows, csv_path)

    if make_plot:
        plot_path = output_dir / "episode_token_trends.png"
        _plot_rows(rows, plot_path, phases=("probe", "action"))
    else:
        plot_path = None

    print(f"input: {input_path}")
    print(f"rows: {len(rows)}")
    print(f"csv: {csv_path}")
    if plot_path is not None:
        print(f"plot: {plot_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compute episode-wise average token usage from prompts_and_responses.jsonl "
            "for probe/action calls."
        )
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Path to prompts_and_responses.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=None,
        help="Output directory for CSV/plot (default: input file directory).",
    )
    parser.add_argument(
        "--encoding",
        default="word_estimate",
        help="Token encoding: word_estimate (fast), auto, or cl100k_base.",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip PNG plot generation.",
    )
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else input_path.parent
    )
    analyze(
        input_path=input_path,
        output_dir=output_dir,
        encoding=args.encoding,
        make_plot=not args.no_plot,
    )


if __name__ == "__main__":
    main()
