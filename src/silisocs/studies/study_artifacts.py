"""Shared study artifact utilities for the experiment runner."""

from __future__ import annotations

import json
import math
import os
import shutil
import statistics
from pathlib import Path
from typing import Any

import yaml

from silisocs.studies.study_types import resolve_study_id

# Two-tailed 95% critical values of the t-distribution, t(0.975, df).
# Beyond df=30 the normal approximation (1.96) is used.
_T_CRITICAL_95: dict[int, float] = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}


def _t_critical_95(df: int) -> float:
    """Return t(0.975, df), falling back to 1.96 for df > 30."""
    return _T_CRITICAL_95.get(df, 1.96)


def _metric_stats(values: list[float]) -> dict[str, Any]:
    """Compute n/mean/stdev and a 95% t-distribution CI for replicate values."""
    n = len(values)
    mean = statistics.fmean(values)
    if n < 2:
        return {"n": n, "mean": mean, "stdev": None, "ci95_low": None, "ci95_high": None}
    stdev = statistics.stdev(values)
    half_width = _t_critical_95(n - 1) * stdev / math.sqrt(n)
    return {
        "n": n,
        "mean": mean,
        "stdev": stdev,
        "ci95_low": mean - half_width,
        "ci95_high": mean + half_width,
    }


def _numeric_values(values: list[Any]) -> list[float] | None:
    """Return float values when every entry is numeric, else None."""
    if not values:
        return None
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        return [float(value) for value in values]
    return None


def resolve_study_definition_path(path: Path) -> Path:
    """Resolve a study path to a YAML file.

    Supports the future directory layout where studies live under
    experiments/studies/<study_name_or_id>/study.yaml.
    """
    if path.is_dir():
        candidate = path / "study.yaml"
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"Study directory does not contain study.yaml: {path}")
    return path


def load_study_definition(path: Path) -> dict[str, Any]:
    """Load a study definition YAML file."""
    study_path = resolve_study_definition_path(path)
    with study_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Study definition must load to a mapping: {study_path}")
    if "study" not in data or "hypotheses" not in data:
        raise ValueError(f"Study definition missing required keys: {study_path}")
    return data


def write_yaml(path: Path, data: dict[str, Any], *, dry_run: bool = False) -> None:
    """Write a study artifact as YAML (``dry_run`` resolves paths without writing)."""
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=False)


def write_json(path: Path, data: Any, *, dry_run: bool = False) -> None:
    """Write a study artifact as pretty JSON (``dry_run`` writes nothing)."""
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)


def create_relative_symlink(target: Path, link: Path, *, dry_run: bool = False) -> None:
    """Create a relative symlink, replacing an existing file or symlink."""
    if link.is_symlink() or link.exists():
        if dry_run:
            return
        if link.is_dir() and not link.is_symlink():
            shutil.rmtree(link)
        else:
            link.unlink()
    rel_target = Path(os.path.relpath(target.resolve(), link.parent.resolve()))
    if dry_run:
        return
    link.symlink_to(rel_target)


def extract_run_metadata(source_dir: Path, config_path: str | None = None) -> dict[str, Any]:
    """Extract reproducibility metadata from a run directory."""
    metadata: dict[str, Any] = {"source": str(source_dir)}
    effective_config = source_dir / "effective_config.yaml"
    if not effective_config.is_file():
        return metadata

    with effective_config.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    sim = cfg.get("sim", {})
    llm = sim.get("llm", {}) if isinstance(sim, dict) else {}
    metadata["model_name"] = llm.get("name")
    metadata["model_config"] = llm.get("provider")

    metadata["scenario"] = cfg.get("scenario_name")

    setting = cfg.get("setting", {}) if isinstance(cfg.get("setting"), dict) else {}
    event = cfg.get("event", {}) if isinstance(cfg.get("event"), dict) else {}
    metadata["world_description"] = (
        event.get("context") or setting.get("background") or setting.get("name")
    )

    metadata["max_steps"] = cfg.get("num_steps")
    metadata["num_agents"] = cfg.get("num_agents")
    metadata["seed"] = cfg.get("seed")

    overrides_path = next((source_dir / "configs").glob("*/overrides.yaml"), None)
    if overrides_path is not None and overrides_path.is_file():
        with overrides_path.open("r", encoding="utf-8") as f:
            cli_overrides: list[str] = yaml.safe_load(f) or []
        metadata["cli_overrides"] = cli_overrides
        entry_point = "uv run python -m silisocs.runtime.runner"
        if config_path:
            entry_point += f" --config-path {config_path}"
        metadata["run_command"] = entry_point + " " + " ".join(cli_overrides)

    return metadata


def _load_eval_payload(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def _combine_eval_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    if not payloads:
        return {
            "aggregated": {},
            "aggregated_stats": {},
            "summary": {},
            "agents": {},
            "checkpoint": None,
        }
    if len(payloads) == 1:
        payload = payloads[0]
        return {
            "aggregated": payload.get("aggregated", {}),
            "aggregated_stats": {},
            "summary": payload.get("summary", {}),
            "agents": payload.get("agents", {}),
            "checkpoint": payload.get("checkpoint"),
        }

    combined: dict[str, Any] = {
        "aggregated": {},
        "aggregated_stats": {},
        "summary": {},
        "agents": {},
        "checkpoint": None,
    }
    metric_names: set[str] = set()
    for payload in payloads:
        metric_names.update(payload.get("aggregated", {}).keys())
    for metric in sorted(metric_names):
        vals = [
            payload["aggregated"][metric]
            for payload in payloads
            if payload.get("aggregated", {}).get(metric) is not None
        ]
        combined["aggregated"][metric] = sum(vals) / len(vals) if vals else None
        numeric_vals = _numeric_values(vals)
        if numeric_vals is not None:
            combined["aggregated_stats"][metric] = _metric_stats(numeric_vals)

    summary_names: set[str] = set()
    for payload in payloads:
        summary_names.update(payload.get("summary", {}).keys())
    for key in sorted(summary_names):
        values = [
            payload["summary"].get(key)
            for payload in payloads
            if payload.get("summary", {}).get(key) is not None
        ]
        if values and all(isinstance(value, (int, float)) for value in values):
            combined["summary"][key] = sum(values)
        else:
            combined["summary"][key] = values[0] if values else None

    agent_names: set[str] = set()
    for payload in payloads:
        agent_names.update(payload.get("agents", {}).keys())
    for agent_name in sorted(agent_names):
        metric_map: dict[str, list[float]] = {}
        for payload in payloads:
            agent_metrics = payload.get("agents", {}).get(agent_name, {})
            for metric, value in agent_metrics.items():
                if value is not None:
                    metric_map.setdefault(metric, []).append(float(value))
        combined["agents"][agent_name] = {
            metric: (sum(vals) / len(vals) if vals else None) for metric, vals in metric_map.items()
        }

    for payload in payloads:
        checkpoint = payload.get("checkpoint")
        if checkpoint is not None:
            combined["checkpoint"] = checkpoint
            break

    return combined


def build_summary(eval_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate eval results into the study-wide summary payload."""
    if not eval_results:
        return {"conditions": [], "metrics_by_condition": {}, "metrics_stats_by_condition": {}}

    conditions: list[dict[str, Any]] = []
    for result in eval_results:
        meta = result.get("_meta", {})
        conditions.append(
            {
                "hypothesis": meta.get("hypothesis"),
                "condition": meta.get("condition"),
                "scenario": meta.get("scenario"),
                "aggregated": result.get("aggregated", {}),
                "summary": result.get("summary", {}),
            }
        )

    by_hyp_cond: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for condition in conditions:
        hyp = condition["hypothesis"] or ""
        cond = condition["condition"] or ""
        by_hyp_cond.setdefault(hyp, {}).setdefault(cond, []).append(condition)

    metrics_by_condition: dict[str, dict[str, dict[str, float | None]]] = {}
    metrics_stats_by_condition: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for hyp_id, conds in by_hyp_cond.items():
        metrics_by_condition[hyp_id] = {}
        metrics_stats_by_condition[hyp_id] = {}
        for cond_name, entries in conds.items():
            metric_names: set[str] = set()
            for entry in entries:
                metric_names.update(entry["aggregated"].keys())
            aggregate: dict[str, float | None] = {}
            aggregate_stats: dict[str, dict[str, Any]] = {}
            for metric in sorted(metric_names):
                vals = [
                    entry["aggregated"][metric]
                    for entry in entries
                    if entry["aggregated"].get(metric) is not None
                ]
                aggregate[metric] = sum(vals) / len(vals) if vals else None
                numeric_vals = _numeric_values(vals)
                if numeric_vals is not None:
                    aggregate_stats[metric] = _metric_stats(numeric_vals)
            metrics_by_condition[hyp_id][cond_name] = aggregate
            metrics_stats_by_condition[hyp_id][cond_name] = aggregate_stats

    return {
        "conditions": conditions,
        "metrics_by_condition": metrics_by_condition,
        "metrics_stats_by_condition": metrics_stats_by_condition,
    }


def organize_study_outputs(
    repo_root: Path,
    study_data: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    output_root: Path | None = None,
    dry_run: bool = False,
    clean: bool = True,
) -> Path:
    """Build the browsable study tree under experiments/studies/{study_id}/generated/organized."""
    study = study_data["study"]
    study_name = str(study["name"]).strip()
    study_id = resolve_study_id(study)
    study_root = repo_root / "experiments" / "studies" / study_id
    organized_root = output_root or (study_root / "generated" / "organized")

    if clean and organized_root.exists() and not dry_run:
        shutil.rmtree(organized_root)
    if not dry_run:
        organized_root.mkdir(parents=True, exist_ok=True)

    study_summary = {
        "name": study_name,
        "study_id": study_id,
        "question": study.get("question"),
        "scenarios": study.get("scenarios", []),
        "hypotheses": list(study_data.get("hypotheses", {}).keys()),
    }
    write_yaml(organized_root / "study_summary.yaml", study_summary, dry_run=dry_run)

    all_eval_payloads: list[dict[str, Any]] = []

    for hyp_id, hyp in study_data.get("hypotheses", {}).items():
        hyp_dir = organized_root / hyp_id
        if not dry_run:
            hyp_dir.mkdir(parents=True, exist_ok=True)

        hypothesis_summary = {
            "id": hyp_id,
            "statement": hyp.get("statement"),
            "independent_variable": hyp.get("independent_variable"),
            "prediction": hyp.get("prediction"),
            "status": hyp.get("status", "testing"),
            "conditions": sorted((hyp.get("conditions") or {}).keys()),
        }
        write_yaml(hyp_dir / "hypothesis.yaml", hypothesis_summary, dry_run=dry_run)

        hyp_records = [record for record in records if record.get("hypothesis") == hyp_id]
        hyp_rows: list[dict[str, Any]] = []

        cond_map = hyp.get("conditions") or {}
        for cond_id in cond_map:
            cond_records = [record for record in hyp_records if record.get("condition") == cond_id]
            for record in cond_records:
                scenario = str(record.get("scenario", "")).strip() or "scenario"
                seed = record.get("seed")
                seed_tag = f"seed_{seed}" if seed is not None else "seed_default"
                seed_dir = hyp_dir / cond_id / scenario / seed_tag

                run_dir_raw = record.get("run_dir") or record.get("simulation_output_path")
                run_dir = Path(str(run_dir_raw)) if run_dir_raw else None
                if run_dir is not None and not run_dir.is_absolute():
                    run_dir = (repo_root / run_dir).resolve()

                if not dry_run:
                    seed_dir.mkdir(parents=True, exist_ok=True)

                metadata: dict[str, Any] = {
                    "run_id": record.get("run_id"),
                    "study_id": study_id,
                    "study": study_name,
                    "hypothesis": hyp_id,
                    "condition": cond_id,
                    "scenario": scenario,
                    "seed": seed,
                    "status": record.get("status"),
                    "execution_mode": record.get("execution_mode"),
                    "started_at": record.get("started_at"),
                    "finished_at": record.get("finished_at"),
                    "run_dir": str(run_dir) if run_dir is not None else None,
                    "evaluations": record.get("evaluations", []),
                    "eval_paths": record.get("eval_paths", {}),
                }
                if run_dir is not None:
                    metadata.update(extract_run_metadata(run_dir))
                write_yaml(seed_dir / "config.yaml", metadata, dry_run=dry_run)

                if run_dir is not None and run_dir.exists():
                    create_relative_symlink(run_dir, seed_dir / "run", dry_run=dry_run)

                eval_dir = seed_dir / "eval"
                if not dry_run:
                    eval_dir.mkdir(parents=True, exist_ok=True)

                # Single pass over the evaluations: resolve each eval_path once,
                # then symlink it and load its payload (a non-file payload is
                # skipped by _load_eval_payload, matching the prior exists()
                # gate on the symlink side).
                first_eval_linked = False
                payloads: list[dict[str, Any]] = []
                for eval_item in record.get("evaluations", []):
                    eval_id = str(eval_item.get("id", "eval")).strip() or "eval"
                    eval_path_raw = eval_item.get("path")
                    if not eval_path_raw:
                        continue
                    eval_path = Path(str(eval_path_raw))
                    if not eval_path.is_absolute():
                        eval_path = (repo_root / eval_path).resolve()
                    if not eval_path.exists():
                        continue

                    eval_target_dir = eval_dir / eval_id
                    if not dry_run:
                        eval_target_dir.mkdir(parents=True, exist_ok=True)
                    create_relative_symlink(
                        eval_path, eval_target_dir / eval_path.name, dry_run=dry_run
                    )

                    if not first_eval_linked:
                        create_relative_symlink(eval_path, seed_dir / "eval.json", dry_run=dry_run)
                        first_eval_linked = True

                    payload = _load_eval_payload(eval_path)
                    if payload is not None:
                        payloads.append(payload)

                if not payloads and record.get("reused", {}).get("eval"):
                    reused_eval = Path(str(record["reused"]["eval"]))
                    if not reused_eval.is_absolute():
                        reused_eval = (repo_root / reused_eval).resolve()
                    payload = _load_eval_payload(reused_eval)
                    if payload is not None:
                        payloads.append(payload)

                combined = _combine_eval_payloads(payloads)
                combined["_meta"] = {
                    "hypothesis": hyp_id,
                    "condition": cond_id,
                    "scenario": scenario,
                }
                hyp_rows.append(
                    {
                        "condition": cond_id,
                        "scenario": scenario,
                        "seed": seed,
                        "run_id": record.get("run_id"),
                        "status": record.get("status"),
                        "run_dir": str(run_dir) if run_dir is not None else None,
                        "eval_paths": record.get("eval_paths", {}),
                        "agents": combined.get("agents", {}),
                        "aggregated": combined.get("aggregated", {}),
                        "aggregated_stats": combined.get("aggregated_stats", {}),
                        "summary": combined.get("summary", {}),
                    }
                )
                if combined.get("aggregated") or combined.get("summary"):
                    all_eval_payloads.append(combined)

        write_json(hyp_dir / "runs.json", hyp_rows, dry_run=dry_run)

    write_json(organized_root / "summary.json", build_summary(all_eval_payloads), dry_run=dry_run)
    return organized_root
