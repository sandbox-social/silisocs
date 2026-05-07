"""Analyze the Echo Chambers H1 random-exposure study.

Compares the five-run scale-free similarity baseline against the five-run
scale-free random-exposure condition, using the paper-aligned metric columns
already emitted by the Echo replication runtime.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import matplotlib.pyplot as plt
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASELINE_ROOT = (
    REPO_ROOT / "replications/echo_chambers/graph_experiments/replication_main/scale_free"
)
DEFAULT_RANDOM_ROOT = (
    REPO_ROOT / "replications/echo_chambers/generated/runs/h1_random_exposure/"
    "scale_free_random_5seed"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "replications/echo_chambers/generated/analysis/h1_random_exposure"

METRICS = [
    ("polarization", "Polarization"),
    ("neighbor_correlation_index", "Neighbor Correlation Index"),
    ("global_disagreement", "Global Disagreement"),
]


@dataclass(frozen=True)
class RunSeries:
    condition: str
    run_id: str
    run_dir: Path
    rows: list[dict[str, float]]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_run(run_dir: Path, condition: str, run_id: str) -> RunSeries:
    metrics_path = run_dir / "echo_metrics.jsonl"
    if not metrics_path.exists():
        raise FileNotFoundError(f"Missing metrics file: {metrics_path}")
    rows: list[dict[str, float]] = []
    for row in _read_jsonl(metrics_path):
        rows.append(
            {
                "step": float(row["step"]),
                "polarization": float(row.get("polarization", row.get("belief_variance"))),
                "neighbor_correlation_index": float(
                    row.get(
                        "neighbor_correlation_index",
                        row.get("pearson_neighbor_correlation"),
                    )
                ),
                "global_disagreement": float(
                    row.get(
                        "global_disagreement",
                        row.get("global_disagreement_degree_normalized"),
                    )
                ),
            }
        )
    return RunSeries(condition=condition, run_id=run_id, run_dir=run_dir, rows=rows)


def _discover_baseline_runs(root: Path) -> list[RunSeries]:
    runs: list[RunSeries] = []
    for path in sorted(root.glob("rep_*/run")):
        runs.append(_load_run(path, "similarity", path.parent.name))
    return runs


def _discover_random_runs(root: Path) -> list[RunSeries]:
    runs: list[RunSeries] = []
    for path in sorted(root.glob("seed_*/run")):
        runs.append(_load_run(path, "random", path.parent.name))
    return runs


def _values_at_step(runs: list[RunSeries], metric: str, step: int) -> list[float]:
    values: list[float] = []
    for run in runs:
        if step < len(run.rows):
            values.append(run.rows[step][metric])
    return values


def _condition_curve(runs: list[RunSeries], metric: str) -> list[dict[str, float]]:
    max_steps = max(len(run.rows) for run in runs)
    curve: list[dict[str, float]] = []
    for step in range(max_steps):
        vals = _values_at_step(runs, metric, step)
        if not vals:
            continue
        sd = stdev(vals) if len(vals) > 1 else 0.0
        se = sd / math.sqrt(len(vals)) if vals else 0.0
        curve.append(
            {
                "step": float(step),
                "mean": float(mean(vals)),
                "sd": float(sd),
                "se": float(se),
                "lo95": float(mean(vals) - 1.96 * se),
                "hi95": float(mean(vals) + 1.96 * se),
                "n": float(len(vals)),
            }
        )
    return curve


def _summary_stats(values: list[float]) -> dict[str, float]:
    sd = stdev(values) if len(values) > 1 else 0.0
    se = sd / math.sqrt(len(values)) if values else 0.0
    return {
        "n": float(len(values)),
        "mean": float(mean(values)) if values else 0.0,
        "sd": float(sd),
        "se": float(se),
        "lo95": float((mean(values) - 1.96 * se) if values else 0.0),
        "hi95": float((mean(values) + 1.96 * se) if values else 0.0),
    }


def _cohens_d(left: list[float], right: list[float]) -> float:
    if len(left) < 2 or len(right) < 2:
        return 0.0
    left_sd = stdev(left)
    right_sd = stdev(right)
    pooled = math.sqrt(
        (((len(left) - 1) * left_sd**2) + ((len(right) - 1) * right_sd**2))
        / max(1, len(left) + len(right) - 2)
    )
    if pooled <= 0:
        return 0.0
    return (mean(right) - mean(left)) / pooled


def _comparison(left: list[float], right: list[float]) -> dict[str, Any]:
    t_stat, p_value = stats.ttest_ind(right, left, equal_var=False)
    return {
        "similarity": _summary_stats(left),
        "random": _summary_stats(right),
        "random_minus_similarity": float(mean(right) - mean(left)),
        "cohens_d_random_minus_similarity": float(_cohens_d(left, right)),
        "welch_t": float(t_stat),
        "welch_p_two_sided": float(p_value),
    }


def _write_summary(
    baseline: list[RunSeries],
    random: list[RunSeries],
    output_dir: Path,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "baseline_runs": [str(run.run_dir) for run in baseline],
        "random_runs": [str(run.run_dir) for run in random],
        "metrics": {},
    }
    rows_for_csv: list[dict[str, Any]] = []
    for metric, _title in METRICS:
        final_similarity = [run.rows[-1][metric] for run in baseline]
        final_random = [run.rows[-1][metric] for run in random]
        delta_similarity = [run.rows[-1][metric] - run.rows[0][metric] for run in baseline]
        delta_random = [run.rows[-1][metric] - run.rows[0][metric] for run in random]
        metric_summary = {
            "final": _comparison(final_similarity, final_random),
            "delta_final_minus_initial": _comparison(delta_similarity, delta_random),
            "curves": {
                "similarity": _condition_curve(baseline, metric),
                "random": _condition_curve(random, metric),
            },
        }
        summary["metrics"][metric] = metric_summary
        for quantity, block in (
            ("final", metric_summary["final"]),
            ("delta_final_minus_initial", metric_summary["delta_final_minus_initial"]),
        ):
            rows_for_csv.append(
                {
                    "metric": metric,
                    "quantity": quantity,
                    "similarity_mean": block["similarity"]["mean"],
                    "similarity_sd": block["similarity"]["sd"],
                    "random_mean": block["random"]["mean"],
                    "random_sd": block["random"]["sd"],
                    "random_minus_similarity": block["random_minus_similarity"],
                    "cohens_d": block["cohens_d_random_minus_similarity"],
                    "welch_t": block["welch_t"],
                    "welch_p_two_sided": block["welch_p_two_sided"],
                }
            )

    (output_dir / "h1_random_exposure_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    with (output_dir / "h1_random_exposure_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_for_csv[0].keys()))
        writer.writeheader()
        writer.writerows(rows_for_csv)
    return summary


def _plot_trajectories(
    baseline: list[RunSeries],
    random: list[RunSeries],
    output_dir: Path,
) -> None:
    colors = {"similarity": "#1f77b4", "random": "#ff7f0e"}
    labels = {"similarity": "Similarity baseline", "random": "Random exposure"}
    fig, axes = plt.subplots(3, 1, figsize=(12, 13), sharex=True)
    for ax, (metric, title) in zip(axes, METRICS, strict=False):
        for run in baseline:
            ax.plot(
                [row["step"] for row in run.rows],
                [row[metric] for row in run.rows],
                color=colors["similarity"],
                alpha=0.2,
                linewidth=1.0,
            )
        for run in random:
            ax.plot(
                [row["step"] for row in run.rows],
                [row[metric] for row in run.rows],
                color=colors["random"],
                alpha=0.2,
                linewidth=1.0,
            )
        for condition, runs in (("similarity", baseline), ("random", random)):
            curve = _condition_curve(runs, metric)
            xs = [row["step"] for row in curve]
            ys = [row["mean"] for row in curve]
            lo = [row["lo95"] for row in curve]
            hi = [row["hi95"] for row in curve]
            ax.fill_between(xs, lo, hi, color=colors[condition], alpha=0.12)
            ax.plot(
                xs,
                ys,
                label=labels[condition],
                color=colors[condition],
                linewidth=3.0,
            )
        ax.set_title(title)
        ax.set_ylabel(title)
        ax.grid(True, alpha=0.25)
        ax.legend()
    axes[-1].set_xlabel("Day / Step")
    fig.tight_layout()
    fig.savefig(output_dir / "h1_random_exposure_trajectories.png", dpi=180)
    plt.close(fig)


def _plot_final_bars(summary: dict[str, Any], output_dir: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    for ax, (metric, title) in zip(axes, METRICS, strict=False):
        block = summary["metrics"][metric]["final"]
        means = [block["similarity"]["mean"], block["random"]["mean"]]
        errors = [block["similarity"]["se"] * 1.96, block["random"]["se"] * 1.96]
        ax.bar(
            ["Similarity", "Random"],
            means,
            yerr=errors,
            color=["#1f77b4", "#ff7f0e"],
            alpha=0.85,
            capsize=5,
        )
        ax.set_title(title)
        ax.set_ylabel("Final value")
        ax.grid(axis="y", alpha=0.25)
        ax.text(
            0.5,
            0.97,
            f"diff={block['random_minus_similarity']:.3f}\np={block['welch_p_two_sided']:.3g}",
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize=9,
        )
    fig.tight_layout()
    fig.savefig(output_dir / "h1_random_exposure_final_bars.png", dpi=180)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-root", type=Path, default=DEFAULT_BASELINE_ROOT)
    parser.add_argument("--random-root", type=Path, default=DEFAULT_RANDOM_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    baseline = _discover_baseline_runs(args.baseline_root)
    random = _discover_random_runs(args.random_root)
    if not baseline:
        raise RuntimeError(f"No baseline runs found below {args.baseline_root}")
    if not random:
        raise RuntimeError(f"No random runs found below {args.random_root}")

    summary = _write_summary(baseline, random, args.output_dir)
    _plot_trajectories(baseline, random, args.output_dir)
    _plot_final_bars(summary, args.output_dir)

    print(f"Baseline runs: {len(baseline)}")
    print(f"Random runs: {len(random)}")
    print(f"Wrote: {args.output_dir / 'h1_random_exposure_summary.json'}")
    print(f"Wrote: {args.output_dir / 'h1_random_exposure_summary.csv'}")
    print(f"Wrote: {args.output_dir / 'h1_random_exposure_trajectories.png'}")
    print(f"Wrote: {args.output_dir / 'h1_random_exposure_final_bars.png'}")


if __name__ == "__main__":
    main()
