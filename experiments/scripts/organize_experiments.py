#!/usr/bin/env python3
"""Organize experiment outputs into a browsable scientific hierarchy.

Reads a study definition YAML (``experiments/studies/{study}/study.yaml``) and builds
the rest of the tree: hypothesis dirs, condition dirs, config/eval snapshots,
and a study_summary.yaml + summary.json at the study level.

Usage:
    uv run python experiments/scripts/organize_experiments.py experiments/studies/style_diversity/study.yaml
    uv run python experiments/scripts/organize_experiments.py experiments/studies/style_diversity/study.yaml --dry-run
    uv run python experiments/scripts/organize_experiments.py experiments/studies/style_diversity/study.yaml --clean
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from study_io import extract_run_metadata, load_study_definition, validate_sources

PROJECT_ROOT = Path(__file__).parent.parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "experiments" / "studies"


# ---------------------------------------------------------------------------
# Symlink creation
# ---------------------------------------------------------------------------


def create_relative_symlink(target: Path, link: Path, *, dry_run: bool = False) -> None:
    """Create a relative symlink from *link* pointing to *target*. Idempotent."""
    if link.is_symlink() or link.exists():
        if dry_run:
            print(f"  [exists] {link}")
            return
        link.unlink()
    rel_target = Path(os.path.relpath(target.resolve(), link.parent.resolve()))
    if dry_run:
        print(f"  [symlink] {link} -> {rel_target}")
        return
    link.symlink_to(rel_target)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def organize_study(data: dict[str, Any], *, dry_run: bool = False) -> Path:  # noqa: C901, PLR0912, PLR0915
    """Build the full experiment tree for one study definition.

    Returns the study output directory.
    """
    study = data["study"]
    study_name = study["name"]
    study_dir: Path = EXPERIMENTS_DIR / study_name

    if not dry_run:
        study_dir.mkdir(parents=True, exist_ok=True)
    print(f"Study: {study_name} -> {study_dir}")

    # -- Write study_summary.yaml (clean metadata, no source paths) --
    study_meta = {
        "name": study_name,
        "question": study["question"],
        "scenarios": study["scenarios"],
        "hypotheses": list(data["hypotheses"].keys()),
    }
    study_summary_yaml = study_dir / "study_summary.yaml"
    if dry_run:
        print(f"  [write] {study_summary_yaml}")
    else:
        with study_summary_yaml.open("w") as f:
            yaml.dump(study_meta, f, default_flow_style=False, sort_keys=False)

    all_eval_results: list[dict[str, Any]] = []

    for hyp_id, hyp in data["hypotheses"].items():
        hyp_dir = study_dir / hyp_id
        if not dry_run:
            hyp_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n  Hypothesis: {hyp_id}")

        # -- Write hypothesis.yaml --
        hyp_meta = {
            "id": hyp_id,
            "statement": hyp["statement"],
            "independent_variable": hyp["independent_variable"],
            "prediction": hyp["prediction"],
            "status": hyp.get("status", "testing"),
            "conditions": list(hyp["conditions"].keys()),
        }
        hyp_yaml = hyp_dir / "hypothesis.yaml"
        if dry_run:
            print(f"    [write] {hyp_yaml}")
        else:
            with hyp_yaml.open("w") as f:
                yaml.dump(hyp_meta, f, default_flow_style=False, sort_keys=False)

        var_name = hyp["independent_variable"]
        hyp_eval_results: list[dict[str, Any]] = []

        for cond_name, cond in hyp["conditions"].items():
            cond_dir = hyp_dir / f"{var_name}={cond_name}"
            if not dry_run:
                cond_dir.mkdir(parents=True, exist_ok=True)
            print(f"    Condition: {var_name}={cond_name}")

            for run in cond["runs"]:
                scenario = run["scenario"]
                source_dir = PROJECT_ROOT / run["source"]
                eval_path = PROJECT_ROOT / run["eval"]

                # Extract timestamp from source dir name
                timestamp = source_dir.name.replace("_", "T", 1)
                run_dir = cond_dir / scenario / f"run_{timestamp}"

                if not dry_run:
                    run_dir.mkdir(parents=True, exist_ok=True)
                print(f"      {scenario}/run_{timestamp}/")

                # 1. Write config.yaml (extracted metadata)
                config_path = run_dir / "config.yaml"
                base_config = study.get("base_config")
                metadata = extract_run_metadata(source_dir, base_config=base_config)
                metadata["condition"] = cond_name
                metadata["hypothesis"] = hyp_id
                if dry_run:
                    print("        [write] config.yaml")
                else:
                    with config_path.open("w") as f:
                        yaml.dump(metadata, f, default_flow_style=False, sort_keys=False)

                # 2. Symlink checkpoints (runner may nest output one level deeper)
                checkpoints_src = source_dir / "checkpoints"
                if not checkpoints_src.is_dir():
                    subdirs = sorted(
                        (p for p in source_dir.iterdir() if p.is_dir()),
                        key=lambda p: p.stat().st_mtime,
                    )
                    for sub in reversed(subdirs):
                        candidate = sub / "checkpoints"
                        if candidate.is_dir():
                            checkpoints_src = candidate
                            break
                checkpoints_link = run_dir / "checkpoints"
                if checkpoints_src.is_dir():
                    create_relative_symlink(checkpoints_src, checkpoints_link, dry_run=dry_run)
                else:
                    print(f"        [skip] no checkpoints dir in {source_dir}")

                # 3. Copy eval.json
                eval_dest = run_dir / "eval.json"
                if eval_path.is_file():
                    if dry_run:
                        print(f"        [copy] eval.json <- {eval_path.name}")
                    else:
                        shutil.copy2(eval_path, eval_dest)
                    # Collect for runs.json and summary
                    with eval_path.open() as f:
                        eval_data = json.load(f)
                    eval_data["_meta"] = {
                        "hypothesis": hyp_id,
                        "condition": cond_name,
                        "scenario": scenario,
                    }
                    hyp_eval_results.append(eval_data)
                    all_eval_results.append(eval_data)
                else:
                    print(f"        [skip] eval file not found: {eval_path}")

        # -- Write runs.json at hypothesis level --
        runs_data = [
            {
                "condition": r["_meta"]["condition"],
                "scenario": r["_meta"]["scenario"],
                "checkpoint": r.get("checkpoint"),
                "agents": r.get("agents", {}),
                "aggregated": r.get("aggregated", {}),
                "summary": r.get("summary", {}),
            }
            for r in hyp_eval_results
        ]
        runs_path = hyp_dir / "runs.json"
        if dry_run:
            print(f"    [write] {runs_path}")
        else:
            with runs_path.open("w") as f:
                json.dump(runs_data, f, indent=2)

    # -- Write summary.json at study level --
    summary = build_summary(all_eval_results)
    summary_path = study_dir / "summary.json"
    if dry_run:
        print(f"\n  [write] {summary_path}")
    else:
        with summary_path.open("w") as f:
            json.dump(summary, f, indent=2)
    print(f"\nDone. Study tree: {study_dir}")
    return study_dir


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------


def build_summary(eval_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate eval results across all hypotheses/conditions into a summary.

    ``metrics_by_condition`` is nested as ``{hypothesis_id: {condition: {metric: value}}}``
    so condition names that appear in multiple hypotheses don't collide.
    Metric names are discovered from the eval data rather than hard-coded.
    """
    if not eval_results:
        return {"conditions": [], "metrics_by_condition": {}}

    conditions: list[dict[str, Any]] = []
    for r in eval_results:
        meta = r.get("_meta", {})
        entry: dict[str, Any] = {
            "hypothesis": meta.get("hypothesis"),
            "condition": meta.get("condition"),
            "scenario": meta.get("scenario"),
            "aggregated": r.get("aggregated", {}),
            "summary": r.get("summary", {}),
        }
        conditions.append(entry)

    # Group by (hypothesis, condition), average each metric across scenarios
    by_hyp_cond: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for c in conditions:
        hyp = c["hypothesis"] or ""
        cond = c["condition"] or ""
        by_hyp_cond.setdefault(hyp, {}).setdefault(cond, []).append(c)

    metrics_by_condition: dict[str, dict[str, dict[str, float | None]]] = {}
    for hyp_id, conds in by_hyp_cond.items():
        metrics_by_condition[hyp_id] = {}
        for cond_name, entries in conds.items():
            # Discover metric names from whatever keys are present
            metric_names: set[str] = set()
            for e in entries:
                metric_names.update(e["aggregated"].keys())
            agg: dict[str, float | None] = {}
            for metric in sorted(metric_names):
                vals = [
                    e["aggregated"][metric]
                    for e in entries
                    if e["aggregated"].get(metric) is not None
                ]
                agg[metric] = sum(vals) / len(vals) if vals else None
            metrics_by_condition[hyp_id][cond_name] = agg

    return {
        "conditions": conditions,
        "metrics_by_condition": metrics_by_condition,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _clean_tree(root: Path) -> None:
    """Remove experiment tree (WSL/NTFS safe).

    shutil.rmtree can fail on NTFS-mounted dirs in WSL, so we walk
    bottom-up: unlink files/symlinks first, then rmdir leaf-to-root.
    """
    for dirpath, dirnames, filenames in os.walk(str(root), topdown=False):
        for name in filenames:
            p = Path(dirpath) / name
            p.unlink()
        for name in dirnames:
            p = Path(dirpath) / name
            if p.is_symlink():
                p.unlink()
            else:
                p.rmdir()
    root.rmdir()


def main() -> None:
    """CLI entry point for organizing experiment outputs."""
    parser = argparse.ArgumentParser(
        description="Organize experiment outputs into a scientific hierarchy.",
    )
    parser.add_argument(
        "study_file",
        type=Path,
        help="Path to study definition YAML (e.g. experiments/style_diversity/study.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without writing anything",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing experiment tree before rebuilding",
    )

    args = parser.parse_args()

    # Resolve relative to PROJECT_ROOT if needed
    study_path = args.study_file
    if not study_path.is_absolute():
        study_path = PROJECT_ROOT / study_path

    if not study_path.is_file():
        print(f"Error: study file not found: {study_path}", file=sys.stderr)
        sys.exit(1)

    data = load_study_definition(study_path)

    # Validate source paths
    errors = validate_sources(data)
    if errors:
        print("Source validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    # Clean if requested
    study_name = data["study"]["name"]
    study_dir = EXPERIMENTS_DIR / study_name
    if args.clean and study_dir.exists():
        if args.dry_run:
            print(f"[dry-run] Would remove: {study_dir}")
        else:
            _clean_tree(study_dir)
            print(f"Cleaned: {study_dir}")

    organize_study(data, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
