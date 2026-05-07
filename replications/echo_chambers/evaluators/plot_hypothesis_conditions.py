"""Auto-generate per-hypothesis condition comparison plots during run_study.

This evaluator is invoked per run (via --run-dir/--output) and rewrites a
hypothesis-level plot over all currently available seed runs under:
replications/echo_chambers/generated/runs/<hypothesis_id>/<condition_id>/seed_*/run
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_ROOT = REPO_ROOT / "replications" / "echo_chambers" / "generated" / "runs"
ANALYSIS_ROOT = REPO_ROOT / "replications" / "echo_chambers" / "generated" / "analysis"
METRICS = (
    ("polarization", "Polarization"),
    ("neighbor_correlation_index", "Neighbor Correlation Index"),
    ("global_disagreement", "Global Disagreement"),
    ("belief_volatility", "Belief Volatility"),
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_series(path: Path) -> list[dict[str, float]]:
    rows = _read_jsonl(path)
    out: list[dict[str, float]] = []
    for row in rows:
        out.append(
            {
                "step": float(row.get("step", 0)),
                "polarization": float(row.get("polarization", row.get("belief_variance", 0.0))),
                "neighbor_correlation_index": float(
                    row.get(
                        "neighbor_correlation_index",
                        row.get("pearson_neighbor_correlation", 0.0),
                    )
                ),
                "global_disagreement": float(
                    row.get(
                        "global_disagreement",
                        row.get("global_disagreement_degree_normalized", 0.0),
                    )
                ),
                "belief_volatility": float(row.get("belief_volatility", 0.0)),
            }
        )
    return out


def _beliefs_from_agent_data(run_dir: Path) -> list[dict[int, int]]:
    data = json.loads((run_dir / "echo_agents_data.json").read_text(encoding="utf-8"))
    max_steps = max(len(item.get("beliefs", [])) for item in data.values())
    out: list[dict[int, int]] = []
    for step in range(max_steps):
        row: dict[int, int] = {}
        for node_raw, item in data.items():
            beliefs = item.get("beliefs", [])
            if not beliefs:
                continue
            idx = min(step, len(beliefs) - 1)
            row[int(node_raw)] = int(beliefs[idx])
        out.append(row)
    return out


def _belief_volatility_series(beliefs_by_step: list[dict[int, int]]) -> list[float]:
    if not beliefs_by_step:
        return []
    out: list[float] = [0.0]
    for i in range(1, len(beliefs_by_step)):
        prev = beliefs_by_step[i - 1]
        cur = beliefs_by_step[i]
        nodes = sorted(set(prev) & set(cur))
        if not nodes:
            out.append(0.0)
            continue
        out.append(float(mean(abs(float(cur[n]) - float(prev[n])) for n in nodes)))
    return out


def _augment_series_with_volatility(run_dir: Path, series: list[dict[str, float]]) -> None:
    if not (run_dir / "echo_agents_data.json").exists():
        return
    beliefs = _beliefs_from_agent_data(run_dir)
    vol = _belief_volatility_series(beliefs)
    if not vol:
        return
    for idx, row in enumerate(series):
        row["belief_volatility"] = float(vol[min(idx, len(vol) - 1)])


def _infer_hypothesis_id(output_path: Path, run_dir: Path) -> str | None:
    parts = output_path.resolve().parts
    if "eval" not in parts:
        run_parts = run_dir.resolve().parts
        if "runs" in run_parts:
            ridx = run_parts.index("runs")
            if ridx + 1 < len(run_parts):
                return run_parts[ridx + 1]
        return None
    idx = parts.index("eval")
    if idx + 1 < len(parts):
        return parts[idx + 1]
    return None


def _resolve_hypothesis_runs_root(hypothesis_id: str, run_dir: Path) -> Path:
    for ancestor in run_dir.resolve().parents:
        if ancestor.name == hypothesis_id:
            return ancestor
    return RUNS_ROOT / hypothesis_id


def _discover_runs(
    hypothesis_id: str, run_dir: Path
) -> dict[str, list[tuple[str, list[dict[str, float]]]]]:
    by_condition: dict[str, list[tuple[str, list[dict[str, float]]]]] = defaultdict(list)
    hyp_root = _resolve_hypothesis_runs_root(hypothesis_id, run_dir)
    if not hyp_root.exists():
        return by_condition
    for condition_dir in sorted(p for p in hyp_root.iterdir() if p.is_dir()):
        cond_id = condition_dir.name
        for seed_dir in sorted(condition_dir.glob("seed_*")):
            run_dir = seed_dir / "run"
            metrics_path = run_dir / "echo_metrics.jsonl"
            if not metrics_path.exists():
                continue
            try:
                series = _load_series(metrics_path)
            except Exception:
                continue
            if series:
                _augment_series_with_volatility(run_dir, series)
                by_condition[cond_id].append((seed_dir.name, series))
    return by_condition


def _condition_mean_curve(
    runs: list[tuple[str, list[dict[str, float]]]], metric_key: str
) -> tuple[list[float], list[float]]:
    max_steps = max(len(series) for _, series in runs)
    xs: list[float] = []
    ys: list[float] = []
    for step in range(max_steps):
        vals = [series[step][metric_key] for _, series in runs if step < len(series)]
        if not vals:
            continue
        xs.append(float(step))
        ys.append(float(mean(vals)))
    return xs, ys


def _plot(
    hypothesis_id: str, runs_by_condition: dict[str, list[tuple[str, list[dict[str, float]]]]]
) -> Path:
    out_dir = ANALYSIS_ROOT / hypothesis_id
    out_dir.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(4, 1, figsize=(12, 16), sharex=True)
    cmap = plt.get_cmap("tab10")

    for metric_idx, (metric_key, metric_title) in enumerate(METRICS):
        ax = axes[metric_idx]
        for i, cond_id in enumerate(sorted(runs_by_condition)):
            color = cmap(i % 10)
            runs = runs_by_condition[cond_id]
            for _, series in runs:
                ax.plot(
                    [row["step"] for row in series],
                    [row[metric_key] for row in series],
                    color=color,
                    alpha=0.18,
                    linewidth=1.0,
                )
            xs, ys = _condition_mean_curve(runs, metric_key)
            ax.plot(xs, ys, color=color, linewidth=2.6, label=f"{cond_id} (n={len(runs)})")
        ax.set_ylabel(metric_title)
        ax.set_title(metric_title)
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8)

    axes[-1].set_xlabel("Day / Step")
    fig.suptitle(f"{hypothesis_id}: condition comparison", fontsize=13)
    fig.tight_layout()
    plot_path = out_dir / f"{hypothesis_id}_condition_comparison.png"
    fig.savefig(plot_path, dpi=180)
    plt.close(fig)
    return plot_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    hypothesis_id = _infer_hypothesis_id(args.output, args.run_dir)
    payload: dict[str, Any] = {
        "status": "skipped",
        "reason": "",
        "hypothesis_id": hypothesis_id,
        "plot_path": None,
        "summary_path": None,
        "conditions": {},
    }
    if not hypothesis_id:
        payload["reason"] = "could_not_infer_hypothesis_from_output_path"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return

    runs_by_condition = _discover_runs(hypothesis_id, args.run_dir)
    if len(runs_by_condition) < 2:
        payload["reason"] = "need_at_least_two_conditions_with_metrics"
        payload["conditions"] = {k: len(v) for k, v in runs_by_condition.items()}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return

    plot_path = _plot(hypothesis_id, runs_by_condition)
    summary_path = ANALYSIS_ROOT / hypothesis_id / f"{hypothesis_id}_condition_summary.json"
    summary = {
        "hypothesis_id": hypothesis_id,
        "plot_path": str(plot_path),
        "conditions": {
            cond_id: {
                "num_runs": len(runs),
                "run_ids": [seed_id for seed_id, _ in runs],
            }
            for cond_id, runs in sorted(runs_by_condition.items())
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    payload.update(
        {
            "status": "success",
            "reason": "",
            "plot_path": str(plot_path),
            "summary_path": str(summary_path),
            "conditions": {k: len(v) for k, v in runs_by_condition.items()},
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
