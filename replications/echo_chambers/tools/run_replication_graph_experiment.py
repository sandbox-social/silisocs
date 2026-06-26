"""Run the in-framework EchoChamberSim graph-structure experiment.

The defaults mirror the upstream main experiment: 50 agents, 30 social days,
gpt-4o-mini dated snapshot, temperature 1.0, similarity recommendation,
unlimited neighbor interactions, no mitigation, and the fixed seed-50 graph
files for small-world, scale-free, and random networks.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path
from statistics import mean
from typing import Any

_THREAD_ENV_KEYS = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

for _thread_env_key in _THREAD_ENV_KEYS:
    os.environ.setdefault(_thread_env_key, "1")

from dotenv import load_dotenv

GRAPH_TYPES = ("scale_free", "random", "small_world")
METRICS = (
    ("polarization", "Polarization"),
    ("neighbor_correlation_index", "Neighbor Correlation Index"),
    ("global_disagreement", "Global Disagreement"),
)


def _set_thread_env(env: dict[str, str]) -> None:
    for key in _THREAD_ENV_KEYS:
        env[key] = "1"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _metric_value(row: dict[str, Any], metric: str) -> float:
    if metric == "polarization":
        return float(row.get("belief_variance", row["polarization"]))
    if metric == "neighbor_correlation_index":
        return float(row.get("pearson_neighbor_correlation", row["neighbor_correlation_index"]))
    if metric == "global_disagreement":
        return float(
            row.get(
                "global_disagreement_degree_normalized",
                row.get("global_disagreement_normalized", row["global_disagreement"]),
            )
        )
    return float(row[metric])


def _load_metric_rows(path: Path) -> list[dict[str, float]]:
    rows = _read_jsonl(path)
    return [
        {
            "step": float(row["step"]),
            "polarization": _metric_value(row, "polarization"),
            "neighbor_correlation_index": _metric_value(row, "neighbor_correlation_index"),
            "global_disagreement": _metric_value(row, "global_disagreement"),
        }
        for row in rows
    ]


def _mean_curve(
    series: list[list[dict[str, float]]], metric: str
) -> tuple[list[float], list[float]]:
    max_len = max(len(rows) for rows in series)
    xs: list[float] = []
    ys: list[float] = []
    for index in range(max_len):
        values = [rows[index][metric] for rows in series if index < len(rows)]
        steps = [rows[index]["step"] for rows in series if index < len(rows)]
        if values:
            xs.append(float(mean(steps)))
            ys.append(float(mean(values)))
    return xs, ys


def _plot(output_root: Path, graph_types: tuple[str, ...], runs: int) -> None:
    # Lazy import: plotting needs the optional [analysis] extra; planning/dry-run does not.
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit(
            "Plotting requires matplotlib. Install the analysis extras: "
            "uv sync --extra analysis (or pip install 'silisocs[analysis]')."
        ) from exc

    colors = {"scale_free": "#1f77b4", "random": "#d62728", "small_world": "#2ca02c"}
    fig, axes = plt.subplots(3, 1, figsize=(12, 14), sharex=True)
    summary: dict[str, Any] = {}
    for graph_type in graph_types:
        graph_series = [
            _load_metric_rows(
                output_root / graph_type / f"rep_{rep:02d}" / "run" / "echo_metrics.jsonl"
            )
            for rep in range(1, runs + 1)
            if (output_root / graph_type / f"rep_{rep:02d}" / "run" / "echo_metrics.jsonl").exists()
        ]
        graph_series = [rows for rows in graph_series if rows]
        if not graph_series:
            continue
        summary[graph_type] = {"runs": len(graph_series)}
        for ax, (metric, label) in zip(axes, METRICS, strict=False):
            for rows in graph_series:
                ax.plot(
                    [row["step"] for row in rows],
                    [row[metric] for row in rows],
                    color=colors[graph_type],
                    alpha=0.16,
                    linewidth=1.0,
                )
            xs, ys = _mean_curve(graph_series, metric)
            ax.plot(xs, ys, color=colors[graph_type], linewidth=3.0, label=f"{graph_type} mean")
            summary[graph_type][metric] = {
                "initial_mean": ys[0],
                "final_mean": ys[-1],
                "delta_mean": ys[-1] - ys[0],
            }
            ax.set_ylabel(label)
            ax.grid(True, alpha=0.25)
    axes[-1].set_xlabel("Engine step")
    for ax in axes:
        ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(output_root / "replication_graph_experiment_metrics.png", dpi=180)
    plt.close(fig)
    (output_root / "replication_graph_experiment_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )


def _network_path(repo_root: Path, graph_type: str, num_agents: int, network_seed: int) -> Path:
    return (
        repo_root
        / "replications"
        / "echo_chambers"
        / "input"
        / "networks"
        / f"{graph_type}_network_num_agents_{num_agents}_seed_{network_seed}.json"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default="/home/sneheel/mastodon-sim")
    parser.add_argument(
        "--output-root",
        default="/home/sneheel/mastodon-sim/replications/echo_chambers/graph_experiments/replication_main",
    )
    parser.add_argument("--network-types", nargs="+", default=list(GRAPH_TYPES))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--network-seed", type=int, default=50)
    parser.add_argument(
        "--replicate-seed-base",
        type=int,
        default=None,
        help=(
            "If set, run rep N with seed replicate_seed_base + N while still "
            "using the fixed --network-seed graph file."
        ),
    )
    parser.add_argument("--num-agents", type=int, default=50)
    parser.add_argument("--replication-days", type=int, default=30)
    parser.add_argument("--recommendation", default="similarity")
    parser.add_argument("--max-interactions", type=int, default=-1)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--model", default="gpt-4o-mini-2024-07-18")
    parser.add_argument("--max-concurrent-actions", type=int, default=12)
    parser.add_argument(
        "--llm-response-mode",
        default="json_object",
        choices=("json_object", "structured_parse", "parse"),
    )
    parser.add_argument("--prompt-variant", default="compat", choices=("compat", "exact"))
    parser.add_argument("--parallel", type=int, default=5)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    output_root = Path(args.output_root).resolve()
    config_path = repo_root / "replications" / "echo_chambers" / "conf"
    output_root.mkdir(parents=True, exist_ok=True)
    load_dotenv(repo_root / ".env")
    if not os.environ.get("OPENAI_API_KEY") and not args.dry_run:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    jobs: list[tuple[str, int, list[str], Path]] = []
    for graph_type in args.network_types:
        network_path = _network_path(repo_root, graph_type, args.num_agents, args.network_seed)
        if not network_path.exists():
            raise FileNotFoundError(network_path)
        for rep in range(1, args.runs + 1):
            replicate_seed = (
                args.replicate_seed_base + rep
                if args.replicate_seed_base is not None
                else args.network_seed
            )
            hydra_dir = output_root / graph_type / f"rep_{rep:02d}"
            metrics_path = hydra_dir / "run" / "echo_metrics.jsonl"
            if args.skip_existing and metrics_path.exists():
                continue
            cmd = [
                sys.executable,
                "-m",
                "silisocs.runtime.runner",
                "--config-path",
                str(config_path),
                f"num_agents={args.num_agents}",
                f"replication_days={args.replication_days}",
                f"num_steps={args.replication_days + 1}",
                f"seed={replicate_seed}",
                f"sim.llm.name={args.model}",
                f"sim.llm.temperature={args.temperature}",
                f"sim.max_concurrent_actions={args.max_concurrent_actions}",
                f"echo_network_type={graph_type}",
                f"echo_network_path={network_path}",
                f"echo_recommendation={args.recommendation}",
                f"echo_max_interactions={args.max_interactions}",
                "echo_mitigation_step=1000",
                "echo_mitigation_perspectives_path=null",
                "echo_mitigation_perspectives_only=false",
                "echo_with_long_memory=true",
                f"echo_llm_response_mode={args.llm_response_mode}",
                f"echo_prompt_variant={args.prompt_variant}",
                f"run_name=graph_main_{graph_type}_{args.recommendation}_seed{replicate_seed}_rep_{rep:02d}",
                f"hydra.run.dir={hydra_dir}",
                "hydra.job.name=run",
            ]
            print(" ".join(cmd), flush=True)
            jobs.append((graph_type, rep, cmd, metrics_path))
    if not args.dry_run:
        runnable_jobs = [
            (graph_type, rep, cmd)
            for graph_type, rep, cmd, metrics_path in jobs
            if not (args.skip_existing and metrics_path.exists())
        ]
        max_workers = max(1, min(args.parallel, len(runnable_jobs))) if runnable_jobs else 1
        print(
            f"Running {len(runnable_jobs)} in-framework jobs with parallel={max_workers}",
            flush=True,
        )
        child_env = dict(os.environ)
        _set_thread_env(child_env)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    subprocess.run,
                    cmd,
                    cwd=repo_root,
                    check=True,
                    env=child_env,
                ): (graph_type, rep)
                for graph_type, rep, cmd in runnable_jobs
            }
            for future in concurrent.futures.as_completed(futures):
                graph_type, rep = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    raise RuntimeError(
                        f"Replication job failed: graph={graph_type} rep={rep}"
                    ) from exc
    if not args.dry_run:
        _plot(output_root, tuple(args.network_types), args.runs)


if __name__ == "__main__":
    main()
