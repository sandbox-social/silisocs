"""favorability_diff_histogram module. Auto-generated module docstring."""

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def make_plot(probe_path: str, jumble_path: str | None, output_dir: Path | None = None):
    """make_plot.

    :param str probe_path:
    :type probe_path: str
    :param str | None jumble_path:
    :type jumble_path: str | None
    :param Path | None output_dir:
    :type output_dir: Path | None
    """
    probe_data: dict = {}
    with open(probe_path, encoding="utf-8") as f:
        for line in f:
            event = json.loads(line)
            agent = event.get("source_user")
            episode = event.get("episode")
            label = event.get("label")
            if label == "Favorability":
                cand = (
                    event.get("data", {}).get("interaction_premise_template", {}).get("candidate")
                )
                score_raw = event.get("data", {}).get("probe_return")
                try:
                    score = int(score_raw)
                except (TypeError, ValueError):
                    continue
                probe_data.setdefault(agent, {}).setdefault(episode, {})[cand] = score

    # --- Get favorability differences for episode 0 and 18 ---
    probe_diff = {}
    for agent, episodes in probe_data.items():
        diff0 = None
        diff18 = None
        if 0 in episodes and "Bill Fredrickson" in episodes[0] and "Bradley Carter" in episodes[0]:
            diff0 = episodes[0]["Bill Fredrickson"] - episodes[0]["Bradley Carter"]
        if (
            18 in episodes
            and "Bill Fredrickson" in episodes[18]
            and "Bradley Carter" in episodes[18]
        ):
            diff18 = episodes[18]["Bill Fredrickson"] - episodes[18]["Bradley Carter"]
        probe_diff[agent] = {"ep0": diff0, "ep18": diff18}

    # --- Load jumbling results if provided ---
    jumble_scores: dict = {}
    if jumble_path:
        with open(jumble_path, encoding="utf-8") as f:
            for line in f:
                event = json.loads(line)
                agent = event.get("original_data", {}).get("agent_name")
                # Only consider poll results, not voting
                if event.get("call_type") == "poll" and "poll_result" in event:
                    candidate, score = event["poll_result"]
                    jumble_scores.setdefault(agent, {})[candidate] = score

    # --- Compute jumble favorability difference ---
    jumble_diff = {}
    for agent, scores in jumble_scores.items():
        if "bill_fredrickson" in scores and "bradley_carter" in scores:
            jumble_diff[agent] = scores["bill_fredrickson"] - scores["bradley_carter"]

    # --- Filter agents who favored Bradley at episode 0 ---
    agents = []
    for a, vals in probe_diff.items():
        ep0 = vals.get("ep0")
        # Narrow the type: only include agents where ep0 is a numeric value and less than 0
        if isinstance(ep0, (int, float)) and ep0 < 0:
            agents.append(a)
    agents = sorted(agents, key=lambda x: x.split()[0])  # sort by first name

    # --- Prepare data for plotting ---
    def safe_float(val):
        """safe_float.

        :param val:
        """
        if val is None:
            return np.nan
        try:
            return float(val)
        except Exception:
            return np.nan

    bar1 = [safe_float(probe_diff[a]["ep0"]) for a in agents]
    bar2 = [safe_float(probe_diff[a]["ep18"]) for a in agents]
    bar3 = [safe_float(jumble_diff.get(a, np.nan)) for a in agents]

    x = np.arange(len(agents))
    width = 0.25

    plt.figure(figsize=(max(8, len(agents) * 0.6), 6))
    plt.bar(x - width, bar1, width, label="Episode 0 (probe)", color="#1f77b4")
    plt.bar(x, bar2, width, label="Episode 18 (probe)", color="#ff7f0e")
    plt.bar(x + width, bar3, width, label="Opposing Opinion (jumble)", color="#2ca02c")
    plt.axhline(0, color="gray", linestyle="--", linewidth=1)
    plt.xticks(x, agents, rotation=45, ha="right")
    plt.ylabel("Favorability Difference (Bill - Bradley)")
    plt.title("Favorability Difference Histogram (Agents favoring Bradley at Episode 0)")
    plt.legend()
    plt.tight_layout()

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / "favorability_difference_histogram.png"
        plt.savefig(out_path, dpi=300, bbox_inches="tight")
        print(f"Saved favorability difference histogram to {out_path}")

    plt.show()


def main():
    """Main."""
    parser = argparse.ArgumentParser(description="Plot favorability difference histogram")
    parser.add_argument("--input", "-i", required=True, help="Path to probe_events.jsonl")
    parser.add_argument(
        "--jumble",
        "-j",
        default=None,
        help="Path to jumbling results jsonl (optional)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=None,
        help="Directory to save the generated plot (optional)",
    )

    args = parser.parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else None
    make_plot(args.input, args.jumble, output_dir)


if __name__ == "__main__":
    main()
