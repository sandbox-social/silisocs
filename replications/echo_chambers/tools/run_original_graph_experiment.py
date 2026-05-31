"""Run the upstream EchoChamberSim graph-structure experiment.

This script keeps the original repository source untouched, runs the paper's
main LLM condition across small-world, scale-free, and random graphs, then
recomputes the paper-style metrics from `agents_data.json`.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import shutil
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

import matplotlib.pyplot as plt
import numpy as np
import openai
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


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")


def _safe_pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=False))
    x_denominator = sum((x - x_mean) ** 2 for x in xs)
    y_denominator = sum((y - y_mean) ** 2 for y in ys)
    if x_denominator <= 0 or y_denominator <= 0:
        return 0.0
    return numerator / ((x_denominator * y_denominator) ** 0.5)


def _neighbors_from_network(path: Path) -> dict[int, list[int]]:
    payload = _read_json(path)
    neighbors: dict[int, list[int]] = {int(node): [] for node in payload["nodes"]}
    for left, right in payload["edges"]:
        left_i = int(left)
        right_i = int(right)
        neighbors.setdefault(left_i, []).append(right_i)
        neighbors.setdefault(right_i, []).append(left_i)
    return neighbors


def _beliefs_from_agents(path: Path) -> list[dict[int, int]]:
    payload = _read_json(path)
    max_steps = max(len(item["beliefs"]) for item in payload.values())
    rows: list[dict[int, int]] = []
    for step in range(max_steps):
        beliefs: dict[int, int] = {}
        for agent_id, item in payload.items():
            beliefs[int(agent_id)] = int(item["beliefs"][min(step, len(item["beliefs"]) - 1)])
        rows.append(beliefs)
    return rows


def _paper_metrics(
    neighbors: dict[int, list[int]], beliefs_by_step: list[dict[int, int]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step, beliefs in enumerate(beliefs_by_step):
        values = [float(beliefs[node]) for node in sorted(beliefs)]
        avg = mean(values)
        variance = mean((value - avg) ** 2 for value in values)
        xs: list[float] = []
        ys: list[float] = []
        disagreement = 0.0
        for node in sorted(beliefs):
            node_neighbors = neighbors.get(node, [])
            xs.append(float(beliefs[node]))
            if node_neighbors:
                neighbor_values = [float(beliefs[neighbor]) for neighbor in node_neighbors]
                ys.append(mean(neighbor_values))
                local = sum((float(beliefs[node]) - value) ** 2 for value in neighbor_values)
                disagreement += local / len(node_neighbors)
            else:
                ys.append(float(beliefs[node]))
        rows.append(
            {
                "step": step,
                "polarization": float(variance),
                "neighbor_correlation_index": float(_safe_pearson(xs, ys)),
                "global_disagreement": float(0.5 * disagreement / max(1, len(beliefs))),
                "mean_belief": float(avg),
            }
        )
    return rows


def _load_metric_rows(path: Path) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _mean_curve(series: list[list[dict[str, float]]], metric: str) -> tuple[list[int], list[float]]:
    max_len = max(len(rows) for rows in series)
    xs: list[int] = []
    ys: list[float] = []
    for index in range(max_len):
        values = [rows[index][metric] for rows in series if index < len(rows)]
        if values:
            xs.append(index)
            ys.append(float(mean(values)))
    return xs, ys


def _plot(output_root: Path, graph_types: tuple[str, ...], runs: int) -> None:
    colors = {"scale_free": "#1f77b4", "random": "#d62728", "small_world": "#2ca02c"}
    fig, axes = plt.subplots(3, 1, figsize=(12, 14), sharex=True)
    summary: dict[str, Any] = {}
    for graph_type in graph_types:
        graph_series = [
            _load_metric_rows(output_root / graph_type / f"rep_{rep:02d}" / "paper_metrics.jsonl")
            for rep in range(1, runs + 1)
            if (output_root / graph_type / f"rep_{rep:02d}" / "paper_metrics.jsonl").exists()
        ]
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
    axes[-1].set_xlabel("Day")
    for ax in axes:
        ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(output_root / "original_graph_experiment_metrics.png", dpi=180)
    plt.close(fig)
    (output_root / "original_graph_experiment_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )


def _run_one_original(
    *,
    original_root: Path,
    output_root: Path,
    graph_type: str,
    rep: int,
    network_seed: int,
    replicate_seed_base: int,
    step_count: int,
    num_agents: int,
    recommendation: str,
    max_interactions: int,
    temperature: float,
    topic: str,
    model_name: str,
    skip_existing: bool,
) -> None:
    import agent as original_agent  # type: ignore
    import mesa  # type: ignore
    import utils  # type: ignore
    from model import World  # type: ignore

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    utils.client = openai.OpenAI(api_key=api_key)

    def _completion_with_timeout(
        messages: str,
        system_messages: str = "You are a helpful assistant.",
        model: str = "gpt-4o-2024-08-06",
        temperature: float = 1.0,
        response_type: type | None = None,
    ):
        completion = utils.client.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system_messages},
                {"role": "user", "content": messages},
            ],
            temperature=temperature,
            response_format=response_type,
            timeout=120,
        )
        return completion.choices[0].message.parsed

    utils.get_completion_from_messages_structured = _completion_with_timeout
    original_agent.get_completion_from_messages_structured = _completion_with_timeout
    original_get_neighbors = mesa.space.NetworkGrid.get_neighbors

    def _agent_neighbors(
        self: object, node_id: int, include_center: bool = False, **kwargs: object
    ):
        neighbors = original_get_neighbors(self, node_id, include_center=include_center, **kwargs)
        if neighbors and hasattr(neighbors[0], "belief"):
            return neighbors
        agents = []
        for node in list(neighbors):
            agents.extend(self.get_cell_list_contents([node]))
        return agents

    mesa.space.NetworkGrid.get_neighbors = _agent_neighbors

    network_file = (
        original_root
        / "data"
        / f"{graph_type}_network_num_agents_{num_agents}_seed_{network_seed}.json"
    )
    neighbors = _neighbors_from_network(network_file)
    run_dir = output_root / graph_type / f"rep_{rep:02d}"
    metrics_path = run_dir / "paper_metrics.jsonl"
    if skip_existing and metrics_path.exists():
        print(f"[skip] original graph={graph_type} rep={rep}", flush=True)
        return

    run_dir.mkdir(parents=True, exist_ok=True)
    replicate_seed = replicate_seed_base + rep
    random.seed(replicate_seed)
    np.random.seed(replicate_seed)
    exp_name = (
        f"agents_{num_agents}_reco_{recommendation}_inter_"
        f"{max_interactions}_temp_{temperature}_seed_{network_seed}_rep_{rep:02d}"
    )
    generated_dir = run_dir.parent / exp_name
    if generated_dir.exists():
        shutil.rmtree(generated_dir)
    print(f"[start] original graph={graph_type} rep={rep}", flush=True)
    model = World(
        num_agents=num_agents,
        leaders=[10, 30],
        network_type=graph_type,
        max_interactions=max_interactions,
        belief_keywords_file="./data/belief_keywords.json",
        exp_name=exp_name,
        exp_dir=str(run_dir.parent),
        load_network=True,
        gpt_model=model_name,
        mitigation_step=1000,
        with_long_memory=True,
        mitigation_perspectives_file=None,
        mitigation_perspectives_only=False,
        temp=temperature,
        topic=topic,
        recommendation=recommendation,
        seed=network_seed,
    )
    model.run_model(step_count)
    if generated_dir != run_dir:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        generated_dir.rename(run_dir)
    rows = _paper_metrics(neighbors, _beliefs_from_agents(run_dir / "agents_data.json"))
    _write_jsonl(metrics_path, rows)
    print(f"[done] original graph={graph_type} rep={rep}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original-root", default="/home/sneheel/EchoChamberSim")
    parser.add_argument(
        "--no-original-venv",
        action="store_true",
        help="Do not re-exec through <original-root>/.venv/bin/python.",
    )
    parser.add_argument(
        "--output-root",
        default="/home/sneheel/mastodon-sim/replications/echo_chambers/graph_experiments/original_main",
    )
    parser.add_argument("--network-types", nargs="+", default=list(GRAPH_TYPES))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--network-seed", type=int, default=50)
    parser.add_argument("--replicate-seed-base", type=int, default=5000)
    parser.add_argument("--step-count", type=int, default=30)
    parser.add_argument("--num-agents", type=int, default=50)
    parser.add_argument("--recommendation", default="similarity")
    parser.add_argument("--max-interactions", type=int, default=-1)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--topic", default="euthanasia")
    parser.add_argument("--model", default="gpt-4o-mini-2024-07-18")
    parser.add_argument("--parallel", type=int, default=5)
    parser.add_argument("--single-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--single-graph-type", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--single-rep", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    original_root = Path(args.original_root).resolve()
    original_python = original_root / ".venv" / "bin" / "python"
    if (
        not args.no_original_venv
        and original_python.exists()
        and Path(sys.executable).resolve() != original_python.resolve()
        and os.environ.get("ECHO_CHAMBER_ORIGINAL_VENV") != "1"
    ):
        env = dict(os.environ)
        env["ECHO_CHAMBER_ORIGINAL_VENV"] = "1"
        _set_thread_env(env)
        os.execve(
            str(original_python),
            [str(original_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            env,
        )

    repo_root = Path("/home/sneheel/mastodon-sim")
    load_dotenv(repo_root / ".env")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and not args.dry_run:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        for graph_type in args.network_types:
            for rep in range(1, args.runs + 1):
                print(f"would run original graph={graph_type} rep={rep}")
        return

    sys.path.insert(0, str(original_root))
    os.chdir(original_root)

    if args.single_run:
        if args.single_graph_type is None or args.single_rep is None:
            raise ValueError("--single-run requires --single-graph-type and --single-rep")
        _run_one_original(
            original_root=original_root,
            output_root=output_root,
            graph_type=args.single_graph_type,
            rep=args.single_rep,
            network_seed=args.network_seed,
            replicate_seed_base=args.replicate_seed_base,
            step_count=args.step_count,
            num_agents=args.num_agents,
            recommendation=args.recommendation,
            max_interactions=args.max_interactions,
            temperature=args.temperature,
            topic=args.topic,
            model_name=args.model,
            skip_existing=args.skip_existing,
        )
        return

    jobs = [
        (graph_type, rep) for graph_type in args.network_types for rep in range(1, args.runs + 1)
    ]
    max_workers = max(1, min(args.parallel, len(jobs)))
    print(f"Running {len(jobs)} original-code jobs with parallel={max_workers}", flush=True)

    def _command(graph_type: str, rep: int) -> list[str]:
        return [
            sys.executable,
            str(Path(__file__).resolve()),
            "--single-run",
            "--no-original-venv",
            "--original-root",
            str(original_root),
            "--output-root",
            str(output_root),
            "--single-graph-type",
            graph_type,
            "--single-rep",
            str(rep),
            "--network-seed",
            str(args.network_seed),
            "--replicate-seed-base",
            str(args.replicate_seed_base),
            "--step-count",
            str(args.step_count),
            "--num-agents",
            str(args.num_agents),
            "--recommendation",
            args.recommendation,
            "--max-interactions",
            str(args.max_interactions),
            "--temperature",
            str(args.temperature),
            "--topic",
            args.topic,
            "--model",
            args.model,
            *(["--skip-existing"] if args.skip_existing else []),
        ]

    child_env = dict(os.environ)
    _set_thread_env(child_env)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                subprocess.run,
                _command(graph_type, rep),
                cwd=original_root,
                check=True,
                env=child_env,
            ): (
                graph_type,
                rep,
            )
            for graph_type, rep in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            graph_type, rep = futures[future]
            try:
                future.result()
            except Exception as exc:
                raise RuntimeError(f"Original job failed: graph={graph_type} rep={rep}") from exc
    _plot(output_root, tuple(args.network_types), args.runs)


if __name__ == "__main__":
    main()
