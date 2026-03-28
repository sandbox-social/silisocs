#!/usr/bin/env python3
"""Aggregate per-active-agent per-category metrics across seeds with significance testing.

Computes posts, reposts, likes, replies per active agent per episode,
plus combined interaction category (reply + like + repost).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean, stdev
from typing import Any


def _compute_permutation_pvalue(
    baseline_values: list[float],
    treatment_values: list[float],
) -> float:
    """Two-sided exact permutation test for paired differences."""
    if not baseline_values or not treatment_values:
        return 1.0
    if len(baseline_values) != len(treatment_values):
        return 1.0

    diffs = [t - b for t, b in zip(treatment_values, baseline_values)]
    obs_stat = sum(abs(d) for d in diffs)

    n = len(diffs)
    count_extreme = 0
    total_perms = 0

    for mask in range(2**n):
        perm_diffs = [diffs[i] if (mask & (1 << i)) else -diffs[i] for i in range(n)]
        perm_stat = sum(abs(d) for d in perm_diffs)
        total_perms += 1
        if perm_stat >= obs_stat:
            count_extreme += 1

    return count_extreme / total_perms if total_perms > 0 else 1.0


def _read_compare_csv(csv_path: str) -> dict[str, Any]:
    """Read a compare metrics CSV and extract key metrics.

    Handles both old (without exclude_final_episode) and new (with exclude_final_episode) formats.
    """
    results = {}
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            arm = str(row.get("arm", "")).strip()

            # Handle both old and new column formats
            configured_steps = int(row.get("configured_num_steps", 0) or 0)
            num_steps = int(row.get("num_steps", 0) or 0)
            exclude_final = str(row.get("exclude_final_episode", "False")).lower() == "true"

            # If new format, use those values; otherwise infer from old format
            if configured_steps == 0:
                configured_steps = num_steps

            results[arm] = {
                "post_count": int(row.get("post_count", 0) or 0),
                "reply_count": int(row.get("reply_count", 0) or 0),
                "like_count": int(row.get("like_count", 0) or 0),
                "repost_count": int(row.get("repost_count", 0) or 0),
                "num_eval_users": int(row.get("num_eval_users", 0) or 0),
                "num_steps": num_steps,
                "configured_num_steps": configured_steps,
                "exclude_final_episode": exclude_final,
                "avg_actions_per_active_agent_per_step": float(
                    row.get("avg_actions_per_active_agent_per_step", 0) or 0
                ),
            }
    return results


def _load_json_compare(json_path: str) -> dict[str, Any]:
    """Load detailed JSON compare metrics including per-step arrays."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    results = {}
    for result in data.get("results", []):
        arm = result.get("arm", "")
        results[arm] = result
    return results


def compute_action_category_per_active_agent(
    baseline: dict,
    treatment: dict,
) -> dict[str, float]:
    """Compute per-active-agent metrics for action categories."""
    metrics = {}

    # Compute per-active-agent-per-step for each category
    # Note: per_step_actions_per_active_agent is already per-active-agent
    # So we just need the per-category breakdown

    baseline_users = baseline.get("num_eval_users", 1)
    baseline_steps = baseline.get("num_steps", 1)

    if baseline_users > 0 and baseline_steps > 0:
        # Annualized per active agent per step for each category
        baseline_posts_per_active = baseline.get("post_count", 0) / (
            baseline_users * baseline_steps
        )
        baseline_replies_per_active = baseline.get("reply_count", 0) / (
            baseline_users * baseline_steps
        )
        baseline_likes_per_active = baseline.get("like_count", 0) / (
            baseline_users * baseline_steps
        )
        baseline_reposts_per_active = baseline.get("repost_count", 0) / (
            baseline_users * baseline_steps
        )
        baseline_interactions_per_active = (
            baseline.get("reply_count", 0)
            + baseline.get("like_count", 0)
            + baseline.get("repost_count", 0)
        ) / (baseline_users * baseline_steps)
    else:
        baseline_posts_per_active = baseline_replies_per_active = baseline_likes_per_active = (
            baseline_reposts_per_active
        ) = baseline_interactions_per_active = 0.0

    treatment_users = treatment.get("num_eval_users", 1)
    treatment_steps = treatment.get("num_steps", 1)

    if treatment_users > 0 and treatment_steps > 0:
        treatment_posts_per_active = treatment.get("post_count", 0) / (
            treatment_users * treatment_steps
        )
        treatment_replies_per_active = treatment.get("reply_count", 0) / (
            treatment_users * treatment_steps
        )
        treatment_likes_per_active = treatment.get("like_count", 0) / (
            treatment_users * treatment_steps
        )
        treatment_reposts_per_active = treatment.get("repost_count", 0) / (
            treatment_users * treatment_steps
        )
        treatment_interactions_per_active = (
            treatment.get("reply_count", 0)
            + treatment.get("like_count", 0)
            + treatment.get("repost_count", 0)
        ) / (treatment_users * treatment_steps)
    else:
        treatment_posts_per_active = treatment_replies_per_active = treatment_likes_per_active = (
            treatment_reposts_per_active
        ) = treatment_interactions_per_active = 0.0

    return {
        "baseline_posts_per_active": baseline_posts_per_active,
        "treatment_posts_per_active": treatment_posts_per_active,
        "baseline_replies_per_active": baseline_replies_per_active,
        "treatment_replies_per_active": treatment_replies_per_active,
        "baseline_likes_per_active": baseline_likes_per_active,
        "treatment_likes_per_active": treatment_likes_per_active,
        "baseline_reposts_per_active": baseline_reposts_per_active,
        "treatment_reposts_per_active": treatment_reposts_per_active,
        "baseline_interactions_per_active": baseline_interactions_per_active,
        "treatment_interactions_per_active": treatment_interactions_per_active,
    }


def main():
    scenario_dir = Path(__file__).parent.parent
    outputs_dir = scenario_dir / "outputs"

    # Define seed files (matching the actual seeds 11-15)
    seeds = [11, 12, 13, 14, 15]
    seed_files = {
        11: outputs_dir / "compare_50x10_seed11.csv",
        12: outputs_dir / "compare_50x10_seed12.csv",
        13: outputs_dir / "compare_50x10_seed13.csv",
        14: outputs_dir / "compare_50x10_seed14.csv",
        15: outputs_dir / "compare_50x10_seed15.csv",
    }

    # Load all seed data
    all_seeds_data = {}
    print(f"Looking for compare files in: {outputs_dir}")
    for seed, csv_path in seed_files.items():
        if csv_path.exists():
            all_seeds_data[seed] = _read_compare_csv(str(csv_path))
            print(f"  Loaded seed {seed}: {csv_path.name}")
        else:
            print(f"  Warning: {csv_path} not found")

    print(f"Loaded {len(all_seeds_data)} seeds total")
    if not all_seeds_data:
        print("No compare files found")
        return

    # Aggregate by arm
    aggregated = {
        "chronological": {
            "posts_per_active": [],
            "replies_per_active": [],
            "likes_per_active": [],
            "reposts_per_active": [],
            "interactions_per_active": [],
        },
        "recsys_twitter": {
            "posts_per_active": [],
            "replies_per_active": [],
            "likes_per_active": [],
            "reposts_per_active": [],
            "interactions_per_active": [],
        },
        "recsys_twhin": {
            "posts_per_active": [],
            "replies_per_active": [],
            "likes_per_active": [],
            "reposts_per_active": [],
            "interactions_per_active": [],
        },
    }

    for seed in sorted(all_seeds_data.keys()):
        seed_data = all_seeds_data[seed]
        baseline = seed_data.get("chronological", {})
        twitter = seed_data.get("recsys_twitter", {})
        twhin = seed_data.get("recsys_twhin", {})

        print(
            f"Processing seed {seed}: baseline={bool(baseline)}, twitter={bool(twitter)}, twhin={bool(twhin)}"
        )

        if baseline and twitter:
            twitter_metrics = compute_action_category_per_active_agent(baseline, twitter)
            aggregated["recsys_twitter"]["posts_per_active"].append(
                twitter_metrics["treatment_posts_per_active"]
            )
            aggregated["recsys_twitter"]["replies_per_active"].append(
                twitter_metrics["treatment_replies_per_active"]
            )
            aggregated["recsys_twitter"]["likes_per_active"].append(
                twitter_metrics["treatment_likes_per_active"]
            )
            aggregated["recsys_twitter"]["reposts_per_active"].append(
                twitter_metrics["treatment_reposts_per_active"]
            )
            aggregated["recsys_twitter"]["interactions_per_active"].append(
                twitter_metrics["treatment_interactions_per_active"]
            )

        if baseline and twhin:
            twhin_metrics = compute_action_category_per_active_agent(baseline, twhin)
            aggregated["recsys_twhin"]["posts_per_active"].append(
                twhin_metrics["treatment_posts_per_active"]
            )
            aggregated["recsys_twhin"]["replies_per_active"].append(
                twhin_metrics["treatment_replies_per_active"]
            )
            aggregated["recsys_twhin"]["likes_per_active"].append(
                twhin_metrics["treatment_likes_per_active"]
            )
            aggregated["recsys_twhin"]["reposts_per_active"].append(
                twhin_metrics["treatment_reposts_per_active"]
            )
            aggregated["recsys_twhin"]["interactions_per_active"].append(
                twhin_metrics["treatment_interactions_per_active"]
            )

        if baseline:
            aggregated["chronological"]["posts_per_active"].append(
                baseline.get("post_count", 0)
                / max(baseline.get("num_eval_users", 1) * baseline.get("num_steps", 1), 1)
            )
            aggregated["chronological"]["replies_per_active"].append(
                baseline.get("reply_count", 0)
                / max(baseline.get("num_eval_users", 1) * baseline.get("num_steps", 1), 1)
            )
            aggregated["chronological"]["likes_per_active"].append(
                baseline.get("like_count", 0)
                / max(baseline.get("num_eval_users", 1) * baseline.get("num_steps", 1), 1)
            )
            aggregated["chronological"]["reposts_per_active"].append(
                baseline.get("repost_count", 0)
                / max(baseline.get("num_eval_users", 1) * baseline.get("num_steps", 1), 1)
            )
            aggregated["chronological"]["interactions_per_active"].append(
                (
                    baseline.get("reply_count", 0)
                    + baseline.get("like_count", 0)
                    + baseline.get("repost_count", 0)
                )
                / max(baseline.get("num_eval_users", 1) * baseline.get("num_steps", 1), 1)
            )

    # Compute statistics and p-values
    categories = [
        "posts_per_active",
        "replies_per_active",
        "likes_per_active",
        "reposts_per_active",
        "interactions_per_active",
    ]

    results = []
    for category in categories:
        baseline_vals = aggregated["chronological"].get(category, [])
        twitter_vals = aggregated["recsys_twitter"].get(category, [])
        twhin_vals = aggregated["recsys_twhin"].get(category, [])

        baseline_mean = mean(baseline_vals) if baseline_vals else 0.0
        baseline_sd = stdev(baseline_vals) if len(baseline_vals) > 1 else 0.0

        twitter_mean = mean(twitter_vals) if twitter_vals else 0.0
        twitter_sd = stdev(twitter_vals) if len(twitter_vals) > 1 else 0.0
        twitter_pval = _compute_permutation_pvalue(baseline_vals, twitter_vals)

        twhin_mean = mean(twhin_vals) if twhin_vals else 0.0
        twhin_sd = stdev(twhin_vals) if len(twhin_vals) > 1 else 0.0
        twhin_pval = _compute_permutation_pvalue(baseline_vals, twhin_vals)

        twhin_vs_twitter_pval = _compute_permutation_pvalue(twitter_vals, twhin_vals)

        results.append(
            {
                "metric": category,
                "baseline_mean": baseline_mean,
                "baseline_sd": baseline_sd,
                "twitter_mean": twitter_mean,
                "twitter_sd": twitter_sd,
                "twitter_vs_baseline_pval": twitter_pval,
                "twhin_mean": twhin_mean,
                "twhin_sd": twhin_sd,
                "twhin_vs_baseline_pval": twhin_pval,
                "twhin_vs_twitter_pval": twhin_vs_twitter_pval,
            }
        )

    # Write CSV
    csv_path = outputs_dir / "aggregate_50x10_5seeds_action_categories.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote: {csv_path}")

    # Write JSON
    json_path = outputs_dir / "aggregate_50x10_5seeds_action_categories.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"results": results, "n_seeds": len(all_seeds_data)}, f, indent=2)

    print(f"Wrote: {json_path}")

    # Print summary
    print("\n=== Action Category Per-Active-Agent Metrics ===")
    for result in results:
        m = result["metric"]
        print(f"\n{m}:")
        print(f"  Chronological: {result['baseline_mean']:.6f} ± {result['baseline_sd']:.6f}")
        print(
            f"  Twitter:       {result['twitter_mean']:.6f} ± {result['twitter_sd']:.6f} (p={result['twitter_vs_baseline_pval']:.4f})"
        )
        print(
            f"  TWHIN:         {result['twhin_mean']:.6f} ± {result['twhin_sd']:.6f} (p={result['twhin_vs_baseline_pval']:.4f})"
        )
        print(f"  TWHIN vs Twitter p-value: {result['twhin_vs_twitter_pval']:.4f}")


if __name__ == "__main__":
    main()
