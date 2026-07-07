"""Scaling benchmark harness (SCALABILITY_PLAN.md, Phase 0).

Runs the real runner end-to-end (fresh subprocess, LLM disabled) at increasing
agent counts and reports startup time, per-step wall-clock, and peak RSS — the
regression gate for the scalability work. LLM calls are no-ops
(``sim.llm.disabled=true``), so what's measured is framework cost: config
composition, agent construction, backend/network setup, and the step loop.

Usage::

    uv run python benchmarks/scaling_benchmark.py --sizes 100 1000 --steps 3
    uv run python benchmarks/scaling_benchmark.py --sizes 5000 --steps 2 \
        --override sim.engine.participation.built_in=all

Each run writes into a throwaway output dir and prints one summary row; pass
``--json out.json`` to also dump machine-readable results for A/B comparison.
"""

from __future__ import annotations

import argparse
import json
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_TIME_BIN = "/usr/bin/time"
_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


def _run_one(
    num_agents: int, num_steps: int, extra_overrides: list[str], keep_output: bool
) -> dict[str, Any]:
    out_dir = Path(tempfile.mkdtemp(prefix=f"silisocs-bench-{num_agents}-"))
    time_prefix = [_TIME_BIN, "-v"] if Path(_TIME_BIN).exists() else []
    cmd = [
        *time_prefix,
        sys.executable,
        "-m",
        "silisocs.runtime.runner",
        f"num_agents={num_agents}",
        f"num_steps={num_steps}",
        "sim.llm.disabled=true",
        "scenario_name=benchmark",
        f"hydra.run.dir={out_dir}/run",
        *extra_overrides,
    ]
    start = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    wall_s = time.perf_counter() - start
    result: dict[str, Any] = {
        "num_agents": num_agents,
        "num_steps": num_steps,
        "wall_s": round(wall_s, 2),
        "returncode": proc.returncode,
    }
    rss = _RSS_RE.search(proc.stderr)
    if rss:
        result["peak_rss_mb"] = round(int(rss.group(1)) / 1024, 1)
    else:
        # Fallback: running max over all child processes so far — accurate per run
        # when sizes ascend (each run's peak exceeds the previous run's).
        child_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        result["peak_rss_mb"] = round(child_kb / 1024, 1)

    metrics_files = list(out_dir.rglob("sim_metrics.json"))
    if metrics_files:
        metrics = json.loads(metrics_files[0].read_text())
        episodes = metrics.get("episode_metrics", [])
        durations = [float(ep.get("duration_s", 0.0)) for ep in episodes]
        result["episodes"] = len(durations)
        result["step_total_s"] = round(sum(durations), 2)
        result["step_mean_s"] = round(sum(durations) / len(durations), 3) if durations else None
        result["startup_s"] = round(wall_s - sum(durations), 2)
    if proc.returncode != 0:
        result["stderr_tail"] = proc.stderr[-2000:]
    if keep_output:
        result["output_dir"] = str(out_dir)
    else:
        shutil.rmtree(out_dir, ignore_errors=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--steps", type=int, default=3)
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        help="Extra Hydra override(s), e.g. sim.engine.step.built_in=flow",
    )
    parser.add_argument("--json", type=Path, default=None, help="Also write results as JSON")
    parser.add_argument("--keep-output", action="store_true", help="Keep run output dirs")
    args = parser.parse_args()

    if not Path(_TIME_BIN).exists():
        print(
            f"note: {_TIME_BIN} not found; peak RSS falls back to a running max "
            "(accurate when --sizes ascend)",
            file=sys.stderr,
        )

    results = []
    header = (
        f"{'N':>8}  {'wall_s':>8}  {'startup_s':>9}  {'step_mean_s':>11}  {'peak_rss_mb':>11}  rc"
    )
    print(header)
    print("-" * len(header))
    for size in args.sizes:
        row = _run_one(size, args.steps, list(args.override), args.keep_output)
        results.append(row)

        def _fmt(key: str, r: dict[str, Any] = row) -> str:
            value = r.get(key)
            return "-" if value is None else str(value)

        print(
            f"{row['num_agents']:>8}  {row['wall_s']:>8}  {_fmt('startup_s'):>9}  "
            f"{_fmt('step_mean_s'):>11}  {_fmt('peak_rss_mb'):>11}  {row['returncode']}"
        )
        if row["returncode"] != 0:
            print(row.get("stderr_tail", ""), file=sys.stderr)

    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
        print(f"\nwrote {args.json}")
    return max((r["returncode"] for r in results), default=0)


if __name__ == "__main__":
    raise SystemExit(main())
