"""Post-hoc metrics for EchoChamberSim replication runs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _safe_pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    mx = mean(xs)
    my = mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
    dx = sum((x - mx) ** 2 for x in xs)
    dy = sum((y - my) ** 2 for y in ys)
    if dx <= 0 or dy <= 0:
        return 0.0
    return num / ((dx * dy) ** 0.5)


def _compute_metrics(
    network: dict[str, Any], beliefs_by_step: list[dict[int, int]]
) -> list[dict[str, Any]]:
    neighbors: dict[int, list[int]] = {int(node): [] for node in network.get("node_ids", [])}
    if not neighbors:
        neighbors = {int(node): [] for node in network.get("nodes", []) if str(node).isdigit()}
    for left, right in network.get("edges", []):
        left_i = int(left)
        right_i = int(right)
        neighbors.setdefault(left_i, []).append(right_i)
        neighbors.setdefault(right_i, []).append(left_i)

    rows: list[dict[str, Any]] = []
    for step, beliefs in enumerate(beliefs_by_step):
        values = [float(beliefs[node]) for node in sorted(beliefs)]
        avg = mean(values) if values else 0.0
        belief_variance = mean([(value - avg) ** 2 for value in values]) if values else 0.0
        xs: list[float] = []
        ys: list[float] = []
        directed_disagreement_sum = 0.0
        degree_normalized_disagreement_sum = 0.0
        same_belief_neighbor_count = 0
        for node in sorted(beliefs):
            node_neighbors = neighbors.get(node, [])
            xs.append(float(beliefs[node]))
            if node_neighbors:
                neighbor_values = [float(beliefs[n]) for n in node_neighbors]
                ys.append(mean(neighbor_values))
                local = sum((float(beliefs[node]) - value) ** 2 for value in neighbor_values)
                directed_disagreement_sum += local
                degree_normalized_disagreement_sum += local / len(node_neighbors)
                same_belief_neighbor_count += sum(
                    1 for neighbor in node_neighbors if beliefs[node] == beliefs[neighbor]
                )
            else:
                ys.append(float(beliefs[node]))
                degree_normalized_disagreement_sum += 0.0
        paper_nci = _safe_pearson(xs, ys)
        paper_global_disagreement = 0.5 * degree_normalized_disagreement_sum / max(1, len(beliefs))
        rows.append(
            {
                "step": step,
                "polarization": float(belief_variance),
                "neighbor_correlation_index": float(paper_nci),
                "global_disagreement": float(paper_global_disagreement),
                "mean_belief": float(avg),
                "belief_variance": float(belief_variance),
                "pearson_neighbor_correlation": float(paper_nci),
                "global_disagreement_degree_normalized": float(paper_global_disagreement),
                "upstream_model_polarization_mean_belief": float(avg),
                "upstream_model_nci_same_belief_per_agent": float(
                    same_belief_neighbor_count / max(1, len(beliefs))
                ),
                "global_disagreement_unnormalized": float(0.5 * directed_disagreement_sum),
            }
        )
    return rows


def _beliefs_from_agent_data(run_dir: Path) -> list[dict[int, int]]:
    data = _read_json(run_dir / "echo_agents_data.json")
    max_steps = max(len(item["beliefs"]) for item in data.values())
    out: list[dict[int, int]] = []
    for step in range(max_steps):
        row: dict[int, int] = {}
        for node_raw, item in data.items():
            beliefs = item["beliefs"]
            index = min(step, len(beliefs) - 1)
            row[int(node_raw)] = int(beliefs[index])
        out.append(row)
    return out


def _belief_volatility_series(beliefs_by_step: list[dict[int, int]]) -> list[float]:
    """Per-step volatility: mean absolute belief change per agent from previous step."""
    if not beliefs_by_step:
        return []
    out: list[float] = [0.0]
    for idx in range(1, len(beliefs_by_step)):
        prev = beliefs_by_step[idx - 1]
        cur = beliefs_by_step[idx]
        nodes = sorted(set(prev) & set(cur))
        if not nodes:
            out.append(0.0)
            continue
        mad = mean(abs(float(cur[n]) - float(prev[n])) for n in nodes)
        out.append(float(mad))
    return out


def build_summary(run_dir: Path) -> dict[str, Any]:
    network = _read_json(run_dir / "echo_network.json")
    beliefs_by_step: list[dict[int, int]] = []
    if (run_dir / "echo_agents_data.json").exists():
        beliefs_by_step = _beliefs_from_agent_data(run_dir)

    metrics_rows = _read_jsonl(run_dir / "echo_metrics.jsonl")
    if not metrics_rows and beliefs_by_step:
        metrics_rows = _compute_metrics(network, beliefs_by_step)

    if beliefs_by_step and metrics_rows:
        vol = _belief_volatility_series(beliefs_by_step)
        for idx, row in enumerate(metrics_rows):
            row["belief_volatility"] = float(vol[min(idx, len(vol) - 1)])

    initial = metrics_rows[0] if metrics_rows else {}
    final = metrics_rows[-1] if metrics_rows else {}
    deltas = {
        key: float(final.get(key, 0.0)) - float(initial.get(key, 0.0))
        for key in (
            "polarization",
            "neighbor_correlation_index",
            "global_disagreement",
            "mean_belief",
            "belief_volatility",
        )
    }
    return {
        "summary_type": "echo_chamber_metrics",
        "run_dir": str(run_dir),
        "steps": len(metrics_rows),
        "initial": initial,
        "final": final,
        "deltas": deltas,
        "metrics": metrics_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = build_summary(run_dir)
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")

    csv_path = output.with_suffix(".csv")
    rows = list(summary.get("metrics", []))
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()
