#!/usr/bin/env python3
"""Run all experiments for a study (stages 1-3), then organize (stage 4).

For each hypothesis x condition x scenario combination not yet registered in
study.yaml, this script:
  1. Runs the simulation via run_experiment.py
  2. Evaluates the output via the study's eval.py
  3. Registers the run in study.yaml

After all runs are complete it calls organize_experiments.py (stage 4).

Conditions are skipped if they already have a registered run for that scenario.
Each condition must have a `cli_override` field specifying the Hydra CLI flag
that sets the independent variable (e.g. `model=gpt4o`). Conditions without
`cli_override` are skipped with a warning.

Usage:
    uv run python experiments/scripts/run_study.py experiments/studies/style_diversity/study.yaml
    uv run python experiments/scripts/run_study.py experiments/studies/style_diversity/study.yaml --hypothesis h1_model_capacity
    uv run python experiments/scripts/run_study.py experiments/studies/style_diversity/study.yaml --dry-run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from study_io import load_study_definition

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def already_registered(hyp: dict[str, Any], cond_name: str, scenario: str) -> bool:
    """Return True if this condition/scenario already has a run in study.yaml."""
    runs = hyp["conditions"].get(cond_name, {}).get("runs", [])
    return any(r["scenario"] == scenario for r in runs)


def find_latest_output_dir(scenario: str) -> Path:
    """Return the most recently modified output dir for a scenario."""
    out_base = PROJECT_ROOT / "outputs" / f"{scenario}_experiment"
    dirs = sorted(out_base.iterdir(), key=lambda p: p.stat().st_mtime)
    if not dirs:
        print(f"ERROR: no output dirs found under {out_base}", file=sys.stderr)
        sys.exit(1)
    return dirs[-1]


def find_checkpoint(source_dir: Path, max_steps: int) -> Path:
    """Return the checkpoint file for the given step count."""
    ckpt = source_dir / "checkpoints" / f"step_{max_steps}_checkpoint.json"
    if not ckpt.is_file():
        print(f"ERROR: checkpoint not found: {ckpt}", file=sys.stderr)
        sys.exit(1)
    return ckpt


def register_run(  # noqa: PLR0913
    study_path: Path,
    hyp_id: str,
    cond_name: str,
    scenario: str,
    source: Path,
    eval_path: Path,
) -> None:
    """Append a completed run entry to study.yaml."""
    with study_path.open() as f:
        data = yaml.safe_load(f)
    run_entry = {
        "scenario": scenario,
        "source": str(source.relative_to(PROJECT_ROOT)),
        "eval": str(eval_path.relative_to(PROJECT_ROOT)),
    }
    data["hypotheses"][hyp_id]["conditions"][cond_name].setdefault("runs", []).append(run_entry)
    with study_path.open("w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def run_cmd(cmd: list[str], *, dry_run: bool) -> None:
    """Print and optionally execute a shell command."""
    print(f"    $ {' '.join(str(x) for x in cmd)}")
    if not dry_run:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)
        if result.returncode != 0:
            sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def run_study(
    study_path: Path,
    hypothesis_filter: str | None,
    dry_run: bool,
) -> None:
    """Orchestrate all experiment runs for a study."""
    data = load_study_definition(study_path)
    study = data["study"]
    study_name = study["name"]
    scenarios = study["scenarios"]
    run_overrides = study.get("run_overrides", {})
    shared_override_args = [f"{k}={v}" for k, v in run_overrides.items()]

    eval_script = PROJECT_ROOT / "experiments" / "studies" / study_name / "eval.py"
    max_steps = int(run_overrides.get("simulation.execution.max_steps", 10))

    for hyp_id, hyp in data["hypotheses"].items():
        if hypothesis_filter and hyp_id != hypothesis_filter:
            continue
        print(f"\nHypothesis: {hyp_id}")

        for cond_name, cond in hyp["conditions"].items():
            cli_override = cond.get("cli_override")
            if not cli_override:
                print(f"  [{cond_name}] no cli_override — skipping")
                continue

            for scenario in scenarios:
                if already_registered(hyp, cond_name, scenario):
                    print(f"  [{cond_name}/{scenario}] already registered — skipping")
                    continue

                print(f"\n  [{cond_name}/{scenario}]")

                # Stage 1: Simulate
                sim_cmd = [
                    "uv",
                    "run",
                    "python",
                    "run_experiment.py",
                    f"scenario={scenario}",
                    *shared_override_args,
                    cli_override,
                ]
                run_cmd(sim_cmd, dry_run=dry_run)

                if dry_run:
                    eval_out = (
                        PROJECT_ROOT
                        / "outputs"
                        / f"eval_{study_name}"
                        / hyp_id
                        / cond_name
                        / scenario
                        / "eval.json"
                    )
                    run_cmd(
                        [
                            "uv",
                            "run",
                            "python",
                            str(eval_script),
                            "<checkpoint>",
                            "-o",
                            str(eval_out),
                        ],
                        dry_run=True,
                    )
                    print(f"    [register] study.yaml <- {hyp_id}/{cond_name}/{scenario}")
                    continue

                # Stage 2: Evaluate
                source_dir = find_latest_output_dir(scenario)
                checkpoint = find_checkpoint(source_dir, max_steps)
                eval_out = (
                    PROJECT_ROOT
                    / "outputs"
                    / f"eval_{study_name}"
                    / hyp_id
                    / cond_name
                    / scenario
                    / "eval.json"
                )
                eval_out.parent.mkdir(parents=True, exist_ok=True)
                eval_cmd = [
                    "uv",
                    "run",
                    "python",
                    str(eval_script),
                    str(checkpoint),
                    "-o",
                    str(eval_out),
                ]
                run_cmd(eval_cmd, dry_run=False)

                # Stage 3: Register
                register_run(study_path, hyp_id, cond_name, scenario, source_dir, eval_out)
                print("    registered in study.yaml")

    # Stage 4: Organize
    organizer = PROJECT_ROOT / "experiments" / "scripts" / "organize_experiments.py"
    print("\nStage 4: organize")
    run_cmd(["uv", "run", "python", str(organizer), str(study_path)], dry_run=dry_run)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for running a study."""
    parser = argparse.ArgumentParser(
        description="Run all experiments for a study and organize results.",
    )
    parser.add_argument(
        "study_file",
        type=Path,
        help="Path to study.yaml (e.g. experiments/studies/style_diversity/study.yaml)",
    )
    parser.add_argument(
        "--hypothesis",
        metavar="ID",
        help="Run only this hypothesis (e.g. h1_model_capacity)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them",
    )
    args = parser.parse_args()

    study_path = args.study_file
    if not study_path.is_absolute():
        study_path = PROJECT_ROOT / study_path

    if not study_path.is_file():
        print(f"Error: study file not found: {study_path}", file=sys.stderr)
        sys.exit(1)

    run_study(study_path, args.hypothesis, args.dry_run)


if __name__ == "__main__":
    main()
