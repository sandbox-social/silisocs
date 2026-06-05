"""Compare action metrics between max_actions=12 and max_actions=20 settings.

Loads existing evaluations and produces a side-by-side comparison with effect sizes.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import numpy as np


def _filter_runs_for_setting(rows: list[dict[str, Any]], suffixmarker: str) -> list[dict[str, Any]]:
    """Filter runs by path marker: 'like_20' for new, absence for old."""
    if suffixmarker == "like_20":
        return [r for r in rows if "like_20" in r["run_dir"]]
    # old setting
    return [r for r in rows if "like_20" not in r["run_dir"]]


def _build_summary_table(
    old_runs: list[dict[str, Any]],
    new_runs: list[dict[str, Any]],
    metrics: list[str],
) -> list[dict[str, Any]]:
    """Build comparison table showing mean, sd, and effect size (Cohen's d) for each metric."""
    comparison_rows = []

    for metric in metrics:
        old_vals = [r[metric] for r in old_runs]
        new_vals = [r[metric] for r in new_runs]

        old_mean = mean(old_vals) if old_vals else 0.0
        old_sd = stdev(old_vals) if len(old_vals) > 1 else 0.0
        new_mean = mean(new_vals) if new_vals else 0.0
        new_sd = stdev(new_vals) if len(new_vals) > 1 else 0.0

        # Cohen's d effect size
        pooled_sd = (
            np.sqrt(
                ((len(old_vals) - 1) * old_sd**2 + (len(new_vals) - 1) * new_sd**2)
                / (len(old_vals) + len(new_vals) - 2)
            )
            if (len(old_vals) + len(new_vals) > 2)
            else 0.0
        )
        cohens_d = (new_mean - old_mean) / pooled_sd if pooled_sd > 0 else 0.0

        # % change
        pct_change = ((new_mean - old_mean) / old_mean * 100) if old_mean != 0 else 0.0

        comparison_rows.append(
            {
                "metric": metric,
                "max_actions_12_mean": old_mean,
                "max_actions_12_sd": old_sd,
                "max_actions_20_mean": new_mean,
                "max_actions_20_sd": new_sd,
                "mean_delta": new_mean - old_mean,
                "pct_change": pct_change,
                "cohens_d": cohens_d,
            }
        )

    return comparison_rows


def main() -> None:
    # Load the combined evaluation (has both old and new runs)
    data_path = Path(
        "scenarios/election_recsys_engagement/outputs/"
        "n50_t10_clean50x10_action_events_maxactions20_seeds11_20_excl_ep10.json"
    )
    data = json.loads(data_path.read_text())
    all_runs = data["per_run"]

    # Separate old and new
    old_runs = _filter_runs_for_setting(all_runs, "")
    new_runs = _filter_runs_for_setting(all_runs, "like_20")

    print(f"Old runs (max_actions=12): {len(old_runs)}")
    print(f"New runs (max_actions=20): {len(new_runs)}")

    # Metrics to compare
    metrics = [
        "total_actions_per_active_agent_episode",
        "posts_per_active_agent_episode",
        "replies_per_active_agent_episode",
        "likes_per_active_agent_episode",
        "reposts_per_active_agent_episode",
        "interactions_per_active_agent_episode",
    ]

    comparison = _build_summary_table(old_runs, new_runs, metrics)

    print("\n" + "=" * 110)
    print("COMPARISON: max_actions=12 vs max_actions=20 (seeds 11-20 only)")
    print("=" * 110)
    print()

    for row in comparison:
        m = row["metric"]
        o_m = row["max_actions_12_mean"]
        o_s = row["max_actions_12_sd"]
        n_m = row["max_actions_20_mean"]
        n_s = row["max_actions_20_sd"]
        delta = row["mean_delta"]
        pct = row["pct_change"]
        d = row["cohens_d"]

        print(f"{m}")
        print(f"  max_actions=12:  {o_m:.6f} ± {o_s:.6f}")
        print(f"  max_actions=20:  {n_m:.6f} ± {n_s:.6f}")
        print(f"  Δ (absolute):    {delta:+.6f}")
        print(f"  Δ (% change):    {pct:+.2f}%")
        print(f"  Cohen's d:       {d:+.3f}")
        print()

    # Summary insight
    print("=" * 110)
    print("KEY OBSERVATIONS:")
    print("=" * 110)

    total_actions_old = next(r["total_actions_per_active_agent_episode"] for r in old_runs)
    total_actions_new = next(r["total_actions_per_active_agent_episode"] for r in new_runs)

    total_row = next(
        r for r in comparison if r["metric"] == "total_actions_per_active_agent_episode"
    )
    reposts_row = next(r for r in comparison if r["metric"] == "reposts_per_active_agent_episode")

    print(
        f"\n1. Total actions increased by {total_row['pct_change']:.2f}% (from {total_row['max_actions_12_mean']:.2f} to {total_row['max_actions_20_mean']:.2f})"
    )
    print("   This makes sense: with max_actions=20, agents can take more actions per episode.")
    print()

    print(
        f"2. Reposts changed most dramatically: {reposts_row['pct_change']:+.2f}% (Cohen's d={reposts_row['cohens_d']:+.3f})"
    )
    print(
        f"   From {reposts_row['max_actions_12_mean']:.2f} to {reposts_row['max_actions_20_mean']:.2f}"
    )
    print()

    # Check for inter-arm differences in new setting
    print("3. Inspecting inter-arm differences in new max_actions=20 setting...")
    print("   (This would require separate arm-level analysis)")


if __name__ == "__main__":
    main()
