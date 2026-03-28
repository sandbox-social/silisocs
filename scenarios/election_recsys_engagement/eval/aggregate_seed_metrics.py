#!/usr/bin/env python3
"""Aggregate per-seed arm metrics and compute paired p-values.

Expected input is one or more CSV files emitted by compare_metrics.py,
usually named like compare_50x10_seed11.csv.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import re
from pathlib import Path
from statistics import mean
from typing import Any


def _safe_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_seed(path: Path, run_dir: str) -> str:
    text = f"{path.name} {run_dir}"
    m = re.search(r"seed[_-]?(\d+)", text, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return "unknown"


def _load_rows(compare_csv_paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for csv_path in compare_csv_paths:
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                run_dir = str(row.get("run_dir", ""))
                row["seed"] = _extract_seed(csv_path, run_dir)
                row["source_csv"] = str(csv_path)
                rows.append(row)
    return rows


def _sample_sd(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / (len(values) - 1))


def _paired_sign_flip_p_value(diffs: list[float]) -> float:
    """Two-sided exact paired sign-flip permutation test on mean(diff)."""
    n = len(diffs)
    if n == 0:
        return 1.0

    observed = abs(mean(diffs))
    total = 2**n
    extreme = 0

    for signs in itertools.product((-1.0, 1.0), repeat=n):
        perm_mean = abs(sum(s * d for s, d in zip(signs, diffs)) / n)
        if perm_mean >= observed - 1e-12:
            extreme += 1

    return extreme / total


def _aggregate_arm_metric(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    by_arm: dict[str, list[float]] = {}
    by_seed: dict[str, set[str]] = {}

    for row in rows:
        arm = str(row.get("arm", ""))
        seed = str(row.get("seed", "unknown"))
        value = _safe_float(str(row.get(metric, "0")))
        by_arm.setdefault(arm, []).append(value)
        by_seed.setdefault(arm, set()).add(seed)

    out: list[dict[str, Any]] = []
    for arm in sorted(by_arm.keys()):
        values = by_arm[arm]
        out.append(
            {
                "arm": arm,
                "metric": metric,
                "n_seeds": len(by_seed.get(arm, set())),
                "mean": mean(values) if values else 0.0,
                "sd": _sample_sd(values),
                "min": min(values) if values else 0.0,
                "max": max(values) if values else 0.0,
            }
        )
    return out


def _paired_comparisons(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    by_seed_arm: dict[str, dict[str, float]] = {}
    for row in rows:
        seed = str(row.get("seed", "unknown"))
        arm = str(row.get("arm", ""))
        value = _safe_float(str(row.get(metric, "0")))
        by_seed_arm.setdefault(seed, {})[arm] = value

    comparisons = [
        ("recsys_twitter", "chronological"),
        ("recsys_twhin", "chronological"),
        ("recsys_twhin", "recsys_twitter"),
    ]

    out: list[dict[str, Any]] = []
    for arm_a, arm_b in comparisons:
        paired_diffs: list[float] = []
        paired_seeds: list[str] = []
        for seed, arm_map in sorted(by_seed_arm.items()):
            if arm_a in arm_map and arm_b in arm_map:
                paired_diffs.append(arm_map[arm_a] - arm_map[arm_b])
                paired_seeds.append(seed)

        out.append(
            {
                "comparison": f"{arm_a}-minus-{arm_b}",
                "metric": metric,
                "n_pairs": len(paired_diffs),
                "paired_seeds": paired_seeds,
                "mean_delta": mean(paired_diffs) if paired_diffs else 0.0,
                "sd_delta": _sample_sd(paired_diffs),
                "p_value_two_sided_sign_flip": _paired_sign_flip_p_value(paired_diffs),
            }
        )

    return out


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_csv(path: Path, arm_summary: list[dict[str, Any]], pvals: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "table",
                "name",
                "metric",
                "n",
                "mean",
                "sd",
                "min",
                "max",
                "mean_delta",
                "sd_delta",
                "p_value_two_sided_sign_flip",
                "paired_seeds",
            ]
        )

        for row in arm_summary:
            writer.writerow(
                [
                    "arm_summary",
                    row["arm"],
                    row["metric"],
                    row["n_seeds"],
                    f"{float(row['mean']):.8f}",
                    f"{float(row['sd']):.8f}",
                    f"{float(row['min']):.8f}",
                    f"{float(row['max']):.8f}",
                    "",
                    "",
                    "",
                    "",
                ]
            )

        for row in pvals:
            writer.writerow(
                [
                    "paired_pvalue",
                    row["comparison"],
                    row["metric"],
                    row["n_pairs"],
                    "",
                    "",
                    "",
                    "",
                    f"{float(row['mean_delta']):.8f}",
                    f"{float(row['sd_delta']):.8f}",
                    f"{float(row['p_value_two_sided_sign_flip']):.8f}",
                    "|".join(str(s) for s in row["paired_seeds"]),
                ]
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate compare_metrics outputs across seeds")
    parser.add_argument(
        "--compare-csv-glob",
        required=True,
        help="Glob for compare_metrics CSV files (e.g., '.../compare_50x10_seed*.csv')",
    )
    parser.add_argument(
        "--metric",
        default="avg_actions_per_agent_per_step",
        help="Metric column to aggregate and test (default: avg_actions_per_agent_per_step)",
    )
    parser.add_argument("--output-json", default="", help="Optional JSON output path")
    parser.add_argument("--output-csv", default="", help="Optional CSV output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    compare_paths = sorted(Path().glob(args.compare_csv_glob))
    if not compare_paths:
        raise FileNotFoundError(f"No files matched --compare-csv-glob: {args.compare_csv_glob}")

    rows = _load_rows(compare_paths)
    if not rows:
        raise RuntimeError("No rows found in compare CSV inputs")

    metric = args.metric
    arm_summary = _aggregate_arm_metric(rows, metric)
    pvals = _paired_comparisons(rows, metric)

    payload = {
        "metric": metric,
        "num_input_files": len(compare_paths),
        "input_files": [str(p) for p in compare_paths],
        "arm_summary": arm_summary,
        "paired_significance": pvals,
    }

    print("=== Arm Summary ===")
    for row in arm_summary:
        print(
            f"{row['arm']}: n={row['n_seeds']} mean={float(row['mean']):.6f} "
            f"sd={float(row['sd']):.6f} min={float(row['min']):.6f} max={float(row['max']):.6f}"
        )

    print("\n=== Paired Significance (two-sided sign-flip permutation) ===")
    for row in pvals:
        print(
            f"{row['comparison']}: n={row['n_pairs']} mean_delta={float(row['mean_delta']):.6f} "
            f"sd_delta={float(row['sd_delta']):.6f} p={float(row['p_value_two_sided_sign_flip']):.6f}"
        )

    if args.output_json:
        _write_json(Path(args.output_json).expanduser().resolve(), payload)
    if args.output_csv:
        _write_csv(Path(args.output_csv).expanduser().resolve(), arm_summary, pvals)


if __name__ == "__main__":
    main()
