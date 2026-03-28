#!/usr/bin/env python3
"""Plan and execute structured simulation studies.

Features:
- schema-version validation
- case/condition override expansion
- seed replication
- multi-scenario loops
- optional exact run command templates
- multiple evaluation hooks per run
- builtin evaluator presets
- reproducibility lock artifacts
- bash generation
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1
DEFAULT_RUNNER_MODULE = "mastodon_sim.runtime.runner"
PROCESS_TIMEOUT_RC = 124
PLAN_PREVIEW_ROWS = 10

BUILTIN_EVAL_PRESETS: dict[str, dict[str, Any]] = {
    "builtin.activity_summary": {
        "command": [
            "uv",
            "run",
            "python",
            "experiments/evals/run_activity_summary.py",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "activity_summary.json",
    },
    "builtin.probe_summary": {
        "command": [
            "uv",
            "run",
            "python",
            "experiments/evals/run_activity_summary.py",
            "--mode",
            "probes",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_summary.json",
    },
    "builtin.action_metrics_detailed": {
        "command": [
            "uv",
            "run",
            "python",
            "-m",
            "mastodon_sim.evaluations.default_evaluators",
            "--mode",
            "action_metrics",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "action_metrics_detailed.json",
    },
    "builtin.probe_metrics_detailed": {
        "command": [
            "uv",
            "run",
            "python",
            "-m",
            "mastodon_sim.evaluations.default_evaluators",
            "--mode",
            "probe_metrics",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_metrics_detailed.json",
    },
    "builtin.probe_binary_detailed": {
        "command": [
            "uv",
            "run",
            "python",
            "-m",
            "mastodon_sim.evaluations.default_evaluators",
            "--mode",
            "probe_binary",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_binary_detailed.json",
    },
    "builtin.probe_numeric_detailed": {
        "command": [
            "uv",
            "run",
            "python",
            "-m",
            "mastodon_sim.evaluations.default_evaluators",
            "--mode",
            "probe_numeric",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_numeric_detailed.json",
    },
    "builtin.probe_choice_detailed": {
        "command": [
            "uv",
            "run",
            "python",
            "-m",
            "mastodon_sim.evaluations.default_evaluators",
            "--mode",
            "probe_choice",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_choice_detailed.json",
    },
    "builtin.probe_freetext_detailed": {
        "command": [
            "uv",
            "run",
            "python",
            "-m",
            "mastodon_sim.evaluations.default_evaluators",
            "--mode",
            "probe_freetext",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_freetext_detailed.json",
    },
}


class StudyConfigError(ValueError):
    """Raised when a study file violates schema rules."""


@dataclass(frozen=True)
class EvalSpec:
    """Evaluation hook configuration for one evaluator."""

    eval_id: str
    enabled: bool
    command: tuple[str, ...]
    input_mode: str
    output_arg: str
    run_dir_arg: str
    static_args: tuple[str, ...]
    explicit_args: dict[str, str]
    include_run_dir_in_explicit: bool
    output_subpath: str
    manage_io_args: bool


@dataclass(frozen=True)
class RunSpec:
    """Concrete executable run specification expanded from study YAML."""

    run_id: str
    study_name: str
    hypothesis_id: str
    condition_id: str
    scenario: str
    seed: int
    execution_mode: str
    overrides: dict[str, Any]
    config_path: str | None
    runner_module: str
    re_evaluate: bool
    command_override: tuple[str, ...] | None = None
    eval_specs: tuple[EvalSpec, ...] = ()
    reused_source: str | None = None
    reused_eval: str | None = None


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise StudyConfigError(f"Study file must load to a mapping: {path}")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=False)


def _ensure_mapping(name: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise StudyConfigError(f"{name} must be a mapping")
    return value


def _ensure_string_list(name: str, value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise StudyConfigError(f"{name} must be a list of strings")
    return value


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_override_value(value: Any) -> str:  # noqa: PLR0911
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if value == "":
            return '""'
        safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/")
        if all(ch in safe for ch in value):
            return value
        return json.dumps(value)
    return json.dumps(value, separators=(",", ":"))


def _merge_overrides(*parts: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in parts:
        out.update(part)
    return out


def _extract_inline_overrides(run_defaults: dict[str, Any]) -> dict[str, Any]:
    known = {
        "config_path",
        "scenario",
        "scenarios",
        "seed",
        "seeds",
        "seed_repeats",
        "seed_start",
        "max_concurrent",
        "timeout_seconds",
        "runner_module",
        "overrides",
    }
    out: dict[str, Any] = {}
    for key, value in run_defaults.items():
        if key in known:
            continue
        if "." in key:
            out[key] = value
    return out


def _resolve_command_tokens(value: Any, label: str) -> tuple[str, ...]:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    if isinstance(value, str) and value.strip():
        return tuple(shlex.split(value))
    raise StudyConfigError(f"{label} must be a non-empty command string or list[str]")


def _format_template_token(token: str, context: dict[str, Any]) -> str:
    formatted = token
    for key, value in context.items():
        formatted = formatted.replace(f"{{{key}}}", str(value))
    return formatted


def _format_command_template(template: tuple[str, ...], context: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_format_template_token(token, context) for token in template)


def _resolve_scenarios(study: dict[str, Any], run_defaults: dict[str, Any]) -> list[str]:
    scenarios = _ensure_string_list("study.scenarios", study.get("scenarios"))
    if not scenarios:
        scenarios = _ensure_string_list("study.base_scenarios", study.get("base_scenarios"))
    if not scenarios:
        scenario = run_defaults.get("scenario")
        if isinstance(scenario, str) and scenario:
            scenarios = [scenario]
    if not scenarios:
        raise StudyConfigError("No scenarios found. Set study.scenarios or run_defaults.scenario")
    return scenarios


def _resolve_seeds(run_defaults: dict[str, Any], node: dict[str, Any]) -> list[int]:  # noqa: C901
    if "seeds" in node:
        seeds = node["seeds"]
        if not isinstance(seeds, list) or not all(isinstance(v, int) for v in seeds):
            raise StudyConfigError("Condition seeds must be a list of ints")
        return seeds

    if "seed" in node:
        if not isinstance(node["seed"], int):
            raise StudyConfigError("Condition seed must be an int")
        return [node["seed"]]

    repeats = node.get("seed_repeats", run_defaults.get("seed_repeats"))
    if repeats is not None:
        if not isinstance(repeats, int) or repeats <= 0:
            raise StudyConfigError("seed_repeats must be a positive int")
        seed_start = node.get("seed_start", run_defaults.get("seed_start", run_defaults.get("seed", 1)))
        if not isinstance(seed_start, int):
            raise StudyConfigError("seed_start must be an int")
        return [seed_start + i for i in range(repeats)]

    if "seeds" in run_defaults:
        seeds = run_defaults["seeds"]
        if not isinstance(seeds, list) or not all(isinstance(v, int) for v in seeds):
            raise StudyConfigError("run_defaults.seeds must be a list of ints")
        return seeds

    seed = run_defaults.get("seed", 1)
    if not isinstance(seed, int):
        raise StudyConfigError("run_defaults.seed must be an int")
    return [seed]


def _resolve_eval_spec(  # noqa: C901
    study_root: Path,
    item: dict[str, Any],
    default_id: str,
    source: str,
) -> EvalSpec:
    if not isinstance(item, dict):
        raise StudyConfigError(f"{source} must be a mapping")

    eval_item = copy.deepcopy(item)
    preset = eval_item.get("preset")
    if preset:
        preset_data = BUILTIN_EVAL_PRESETS.get(str(preset))
        if preset_data is None:
            raise StudyConfigError(f"Unknown evaluation preset '{preset}' in {source}")
        merged = copy.deepcopy(preset_data)
        merged.update(eval_item)
        eval_item = merged

    eval_id = str(eval_item.get("id", default_id)).strip()
    if not eval_id:
        raise StudyConfigError(f"{source}.id must be non-empty")

    command = _resolve_command_tokens(eval_item.get("command"), f"{source}.command")
    resolved_cmd: list[str] = []
    for token in command:
        if token.startswith("./"):
            resolved_cmd.append(str((study_root / token).resolve()))
        else:
            resolved_cmd.append(token)

    input_mode = str(eval_item.get("input_mode", "run_dir")).strip()
    if input_mode not in {"run_dir", "explicit_paths"}:
        raise StudyConfigError(f"{source}.input_mode must be run_dir or explicit_paths")

    explicit_args = {
        "checkpoint": "--checkpoint-path",
        "effective_config": "--effective-config-path",
        "metrics": "--metrics-path",
        "prompts": "--prompts-path",
        "run_stats": "--run-stats-path",
    }
    explicit_cfg = _ensure_mapping(f"{source}.explicit_args", eval_item.get("explicit_args"))
    for key, value in explicit_cfg.items():
        if key not in explicit_args:
            raise StudyConfigError(
                f"{source}.explicit_args has unknown key '{key}' (allowed: {sorted(explicit_args)})"
            )
        if not isinstance(value, str) or not value:
            raise StudyConfigError(f"{source}.explicit_args.{key} must be non-empty string")
        explicit_args[key] = value

    output_subpath = str(eval_item.get("output_subpath", "eval.json")).strip()
    if not output_subpath:
        raise StudyConfigError(f"{source}.output_subpath must be non-empty")

    return EvalSpec(
        eval_id=eval_id,
        enabled=bool(eval_item.get("enabled", True)),
        command=tuple(resolved_cmd),
        input_mode=input_mode,
        output_arg=str(eval_item.get("output_arg", "--output")),
        run_dir_arg=str(eval_item.get("run_dir_arg", "--run-dir")),
        static_args=tuple(str(x) for x in eval_item.get("static_args", [])),
        explicit_args=explicit_args,
        include_run_dir_in_explicit=bool(eval_item.get("include_run_dir_in_explicit", True)),
        output_subpath=output_subpath,
        manage_io_args=bool(eval_item.get("manage_io_args", True)),
    )


def _merge_eval_specs(base: tuple[EvalSpec, ...], extra: tuple[EvalSpec, ...], mode: str) -> tuple[EvalSpec, ...]:
    norm_mode = str(mode or "append").strip().lower()
    if norm_mode not in {"append", "replace"}:
        raise StudyConfigError("evaluation_mode must be 'append' or 'replace'")
    if norm_mode == "replace":
        dedup: dict[str, EvalSpec] = {spec.eval_id: spec for spec in extra}
        return tuple(dedup.values())

    dedup: dict[str, EvalSpec] = {spec.eval_id: spec for spec in base}
    for spec in extra:
        dedup[spec.eval_id] = spec
    return tuple(dedup.values())


def _resolve_eval_specs(study_root: Path, study_data: dict[str, Any]) -> tuple[EvalSpec, ...]:
    raw_items: list[dict[str, Any]] = []

    legacy = study_data.get("evaluation")
    if legacy is not None:
        if not isinstance(legacy, dict):
            raise StudyConfigError("evaluation must be a mapping")
        legacy_item = copy.deepcopy(legacy)
        legacy_item.setdefault("id", "default")
        raw_items.append(legacy_item)

    multi = study_data.get("evaluations")
    if multi is not None:
        if not isinstance(multi, list):
            raise StudyConfigError("evaluations must be a list of mappings")
        for idx, item in enumerate(multi):
            if not isinstance(item, dict):
                raise StudyConfigError(f"evaluations[{idx}] must be a mapping")
            raw_items.append(item)

    specs: list[EvalSpec] = []
    for idx, item in enumerate(raw_items):
        spec = _resolve_eval_spec(study_root, item, f"eval_{idx + 1}", f"evaluation[{idx}]")
        specs.append(spec)

    dedup: dict[str, EvalSpec] = {}
    for spec in specs:
        dedup[spec.eval_id] = spec
    return tuple(dedup.values())


def _resolve_condition_eval_specs(
    study_root: Path,
    condition_node: dict[str, Any],
) -> tuple[EvalSpec, ...]:
    raw = condition_node.get("evaluations")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise StudyConfigError("condition evaluations must be a list")

    specs: list[EvalSpec] = []
    for idx, item in enumerate(raw):
        spec = _resolve_eval_spec(
            study_root,
            item,
            default_id=f"condition_eval_{idx + 1}",
            source=f"condition.evaluations[{idx}]",
        )
        specs.append(spec)
    dedup: dict[str, EvalSpec] = {}
    for spec in specs:
        dedup[spec.eval_id] = spec
    return tuple(dedup.values())


def _validate_schema(study_data: dict[str, Any]) -> None:  # noqa: C901,PLR0912
    schema_version = study_data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise StudyConfigError(
            f"Unsupported schema_version={schema_version}; expected {SCHEMA_VERSION}"
        )

    study = _ensure_mapping("study", study_data.get("study"))
    if not isinstance(study.get("name"), str) or not study["name"].strip():
        raise StudyConfigError("study.name is required and must be a non-empty string")

    run_defaults = _ensure_mapping("study.run_defaults", study.get("run_defaults"))
    if "overrides" in run_defaults and not isinstance(run_defaults["overrides"], dict):
        raise StudyConfigError("study.run_defaults.overrides must be a mapping")

    hypotheses = _ensure_mapping("hypotheses", study_data.get("hypotheses"))
    if not hypotheses:
        raise StudyConfigError("hypotheses must include at least one hypothesis")

    for hyp_id, hyp_node in hypotheses.items():
        if not isinstance(hyp_node, dict):
            raise StudyConfigError(f"hypotheses.{hyp_id} must be a mapping")
        if "conditions" not in hyp_node and "cases" not in hyp_node:
            raise StudyConfigError(f"hypotheses.{hyp_id} must define conditions (or cases)")

        cond_map = hyp_node.get("conditions", hyp_node.get("cases"))
        if not isinstance(cond_map, dict) or not cond_map:
            raise StudyConfigError(f"hypotheses.{hyp_id}.conditions must be a non-empty mapping")

        for cond_id, cond_node in cond_map.items():
            if not isinstance(cond_node, dict):
                raise StudyConfigError(f"hypotheses.{hyp_id}.conditions.{cond_id} must be a mapping")
            overrides = cond_node.get("overrides", {})
            if not isinstance(overrides, dict):
                raise StudyConfigError(
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.overrides must be a mapping"
                )
            execution = _ensure_mapping(
                f"hypotheses.{hyp_id}.conditions.{cond_id}.execution",
                cond_node.get("execution"),
            )
            mode = str(execution.get("mode", "run"))
            if mode not in {"run", "reuse_existing"}:
                raise StudyConfigError(
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.execution.mode must be run or reuse_existing"
                )
            if "command" in execution:
                _resolve_command_tokens(
                    execution.get("command"),
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.execution.command",
                )
            if mode == "reuse_existing":
                reuse = _ensure_mapping(
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.reuse",
                    cond_node.get("reuse"),
                )
                runs = reuse.get("runs")
                if not isinstance(runs, list) or not runs:
                    raise StudyConfigError(
                        f"hypotheses.{hyp_id}.conditions.{cond_id}.reuse.runs must be a non-empty list"
                    )


def _expand_runs(  # noqa: C901, PLR0912, PLR0915
    study_path: Path, study_data: dict[str, Any]
) -> tuple[list[RunSpec], tuple[EvalSpec, ...], dict[str, Any]]:
    _validate_schema(study_data)

    study_root = study_path.parent
    study = _ensure_mapping("study", study_data["study"])
    hypotheses = _ensure_mapping("hypotheses", study_data["hypotheses"])
    run_defaults = _ensure_mapping("study.run_defaults", study.get("run_defaults"))
    base_scenarios = _resolve_scenarios(study, run_defaults)

    global_eval_specs = _resolve_eval_specs(study_root, study_data)

    default_overrides = _merge_overrides(
        _extract_inline_overrides(run_defaults),
        _ensure_mapping("study.run_defaults.overrides", run_defaults.get("overrides")),
    )

    config_path = run_defaults.get("config_path")
    if config_path is not None and not isinstance(config_path, str):
        raise StudyConfigError("study.run_defaults.config_path must be a string")

    runner_module = str(run_defaults.get("runner_module", DEFAULT_RUNNER_MODULE))

    run_specs: list[RunSpec] = []
    study_name = str(study["name"]).strip()

    for hyp_id, hyp_node_any in hypotheses.items():
        hyp_node = _ensure_mapping(f"hypotheses.{hyp_id}", hyp_node_any)
        hyp_overrides = _ensure_mapping(
            f"hypotheses.{hyp_id}.overrides", hyp_node.get("overrides")
        )
        conds = hyp_node.get("conditions", hyp_node.get("cases"))
        cond_map = _ensure_mapping(f"hypotheses.{hyp_id}.conditions", conds)

        for cond_id, cond_node_any in cond_map.items():
            cond_node = _ensure_mapping(f"hypotheses.{hyp_id}.conditions.{cond_id}", cond_node_any)
            cond_overrides = _ensure_mapping(
                f"hypotheses.{hyp_id}.conditions.{cond_id}.overrides",
                cond_node.get("overrides"),
            )
            cond_scenarios = _ensure_string_list(
                f"hypotheses.{hyp_id}.conditions.{cond_id}.scenarios",
                cond_node.get("scenarios"),
            )
            scenarios = cond_scenarios or base_scenarios
            execution = _ensure_mapping(
                f"hypotheses.{hyp_id}.conditions.{cond_id}.execution",
                cond_node.get("execution"),
            )
            mode = str(execution.get("mode", "run"))
            re_evaluate = bool(execution.get("re_evaluate", False))

            condition_eval_specs = _resolve_condition_eval_specs(study_root, cond_node)
            eval_mode = str(cond_node.get("evaluation_mode", "append"))
            merged_eval_specs = _merge_eval_specs(global_eval_specs, condition_eval_specs, eval_mode)

            command_template: tuple[str, ...] | None = None
            if "command" in execution:
                command_template = _resolve_command_tokens(
                    execution.get("command"),
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.execution.command",
                )

            if mode == "reuse_existing":
                reuse = _ensure_mapping(
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.reuse",
                    cond_node.get("reuse"),
                )
                reuse_runs = reuse.get("runs", [])
                for idx, ref in enumerate(reuse_runs):
                    if not isinstance(ref, dict):
                        raise StudyConfigError(
                            f"hypotheses.{hyp_id}.conditions.{cond_id}.reuse.runs[{idx}] must be mapping"
                        )
                    source = ref.get("source")
                    if not isinstance(source, str) or not source:
                        raise StudyConfigError(
                            f"hypotheses.{hyp_id}.conditions.{cond_id}.reuse.runs[{idx}].source is required"
                        )
                    scenario = ref.get("scenario", scenarios[0])
                    seed = ref.get("seed", run_defaults.get("seed", 1))
                    if not isinstance(scenario, str) or not scenario:
                        raise StudyConfigError(
                            f"hypotheses.{hyp_id}.conditions.{cond_id}.reuse.runs[{idx}].scenario must be non-empty string"
                        )
                    if not isinstance(seed, int):
                        raise StudyConfigError(
                            f"hypotheses.{hyp_id}.conditions.{cond_id}.reuse.runs[{idx}].seed must be int"
                        )
                    run_specs.append(
                        RunSpec(
                            run_id=f"{hyp_id}__{cond_id}__reuse_{idx}",
                            study_name=study_name,
                            hypothesis_id=hyp_id,
                            condition_id=cond_id,
                            scenario=scenario,
                            seed=seed,
                            execution_mode=mode,
                            overrides={},
                            config_path=config_path,
                            runner_module=runner_module,
                            re_evaluate=re_evaluate,
                            command_override=None,
                            eval_specs=merged_eval_specs,
                            reused_source=source,
                            reused_eval=ref.get("eval"),
                        )
                    )
                continue

            seeds = _resolve_seeds(run_defaults, cond_node)
            merged = _merge_overrides(default_overrides, hyp_overrides, cond_overrides)

            for scenario in scenarios:
                for seed in seeds:
                    run_id = f"{hyp_id}__{cond_id}__{scenario}__seed{seed}"
                    command_override = None
                    if command_template is not None:
                        command_override = _format_command_template(
                            command_template,
                            {
                                "run_id": run_id,
                                "study_name": study_name,
                                "hypothesis_id": hyp_id,
                                "condition_id": cond_id,
                                "scenario": scenario,
                                "seed": seed,
                            },
                        )

                    run_specs.append(
                        RunSpec(
                            run_id=run_id,
                            study_name=study_name,
                            hypothesis_id=hyp_id,
                            condition_id=cond_id,
                            scenario=scenario,
                            seed=seed,
                            execution_mode=mode,
                            overrides=copy.deepcopy(merged),
                            config_path=config_path,
                            runner_module=runner_module,
                            re_evaluate=re_evaluate,
                            command_override=command_override,
                            eval_specs=merged_eval_specs,
                        )
                    )

    return run_specs, global_eval_specs, study


def _build_run_command(spec: RunSpec) -> list[str]:
    if spec.command_override:
        return list(spec.command_override)

    run_name = (
        f"{spec.study_name}_{spec.hypothesis_id}_{spec.condition_id}_{spec.scenario}_seed{spec.seed}"
    )

    cmd = [
        "uv",
        "run",
        "python",
        "-m",
        spec.runner_module,
    ]
    if spec.config_path:
        cmd.extend(["--config-path", spec.config_path])

    cmd.append(f"scenario={spec.scenario}")
    cmd.append(f"sim.seed={spec.seed}")
    cmd.append(f"sim.run_name={run_name}")
    cmd.append(f"experiment_name={spec.study_name}")

    for key in sorted(spec.overrides):
        if key in {"sim.seed", "sim.run_name", "experiment_name", "scenario"}:
            continue
        cmd.append(f"{key}={_normalize_override_value(spec.overrides[key])}")

    return cmd


def _extract_output_dir_from_line(line: str) -> str | None:
    line = line.strip()
    if line.startswith("Output directory:"):
        return line.split("Output directory:", 1)[1].strip()
    marker = "output_dir="
    if marker in line:
        return line.split(marker, 1)[1].strip().split()[0]
    return None


def _find_latest_checkpoint(run_dir: Path) -> Path | None:
    checkpoint_dir = run_dir / "checkpoints"
    if not checkpoint_dir.is_dir():
        return None
    checkpoints = sorted(checkpoint_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return checkpoints[-1] if checkpoints else None


def _run_subprocess(
    command: list[str],
    cwd: Path,
    log_path: Path,
    timeout_seconds: int | None,
) -> tuple[int, list[str], str | None]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_tail: deque[str] = deque(maxlen=40)
    parsed_output_dir: str | None = None

    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                log_file.write(line)
                output_tail.append(line.rstrip("\n"))
                out_dir = _extract_output_dir_from_line(line)
                if out_dir:
                    parsed_output_dir = out_dir
            return_code = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            output_tail.append(f"TIMEOUT after {timeout_seconds}s")
            return_code = PROCESS_TIMEOUT_RC

    return return_code, list(output_tail), parsed_output_dir


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=False)


def _write_jsonl_line(path: Path, obj: Any, lock: threading.Lock | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, sort_keys=False)
    if lock is None:
        with path.open("a", encoding="utf-8") as f:
            f.write(payload + "\n")
        return
    with lock, path.open("a", encoding="utf-8") as f:
        f.write(payload + "\n")


def _enrich_study_with_results(study_data: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    enriched = copy.deepcopy(study_data)
    generated = {
        "generated_at": _now_iso(),
        "schema_version": SCHEMA_VERSION,
        "records": records,
    }
    enriched["generated"] = generated
    return enriched


def _plan_rows(run_specs: list[RunSpec]) -> list[dict[str, Any]]:
    return [
        {
            "run_id": spec.run_id,
            "hypothesis": spec.hypothesis_id,
            "condition": spec.condition_id,
            "scenario": spec.scenario,
            "seed": spec.seed,
            "mode": spec.execution_mode,
            "reused_source": spec.reused_source,
            "re_evaluate": spec.re_evaluate,
            "overrides": spec.overrides,
            "command": _build_run_command(spec) if spec.execution_mode == "run" else None,
            "evaluators": [e.eval_id for e in spec.eval_specs],
        }
        for spec in run_specs
    ]


def _render_bash_script(run_specs: list[RunSpec], repo_root: Path) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"cd {shlex.quote(str(repo_root))}",
        "",
        "# Generated by run_study.py",
    ]
    for spec in run_specs:
        if spec.execution_mode == "reuse_existing":
            lines.append(f"# reuse_existing: {spec.run_id} source={spec.reused_source}")
            continue
        cmd = _build_run_command(spec)
        lines.append(" ".join(shlex.quote(token) for token in cmd))
    lines.append("")
    return "\n".join(lines)


def _resolve_eval_command(
    eval_spec: EvalSpec,
    run_dir: Path,
    eval_output: Path,
    context: dict[str, Any],
) -> list[str]:
    cmd = list(_format_command_template(eval_spec.command, context))
    cmd.extend(_format_command_template(eval_spec.static_args, context))

    if not eval_spec.manage_io_args:
        return cmd

    if eval_spec.input_mode == "run_dir":
        cmd.extend([eval_spec.run_dir_arg, str(run_dir)])
    else:
        checkpoint = _find_latest_checkpoint(run_dir)
        explicit_map: dict[str, Path | None] = {
            "checkpoint": checkpoint,
            "effective_config": run_dir / "effective_config.yaml",
            "metrics": run_dir / "metrics.json",
            "prompts": run_dir / "prompts_and_responses.jsonl",
            "run_stats": run_dir / "run_stats.log",
        }
        for key, path in explicit_map.items():
            if path is not None and Path(path).is_file():
                cmd.extend([eval_spec.explicit_args[key], str(path)])
        if eval_spec.include_run_dir_in_explicit:
            cmd.extend([eval_spec.run_dir_arg, str(run_dir)])

    cmd.extend([eval_spec.output_arg, str(eval_output)])
    return cmd


def _run_evaluations(
    spec: RunSpec,
    run_dir: Path,
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for eval_spec in spec.eval_specs:
        eval_output = (
            generated_dir
            / "eval"
            / eval_spec.eval_id
            / spec.hypothesis_id
            / spec.condition_id
            / spec.scenario
            / f"seed_{spec.seed}"
            / eval_spec.output_subpath
        )
        eval_log = eval_output.with_suffix(eval_output.suffix + ".log")
        eval_context = {
            "run_id": spec.run_id,
            "study_name": spec.study_name,
            "hypothesis_id": spec.hypothesis_id,
            "condition_id": spec.condition_id,
            "scenario": spec.scenario,
            "seed": spec.seed,
            "run_dir": str(run_dir),
            "output_path": str(eval_output),
        }

        if not eval_spec.enabled:
            records.append(
                {
                    "id": eval_spec.eval_id,
                    "status": "disabled",
                    "path": str(eval_output),
                    "command": [],
                    "log_path": str(eval_log),
                    "return_code": None,
                    "tail": [],
                }
            )
            continue

        cmd = _resolve_eval_command(eval_spec, run_dir, eval_output, eval_context)
        rc, tail, _ = _run_subprocess(cmd, repo_root, eval_log, timeout_seconds)
        records.append(
            {
                "id": eval_spec.eval_id,
                "status": "success" if rc == 0 else "failed",
                "path": str(eval_output),
                "command": cmd,
                "log_path": str(eval_log),
                "return_code": rc,
                "tail": tail,
            }
        )

    return records


def _run_one_spec(
    spec: RunSpec,
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
) -> dict[str, Any]:
    started = _now_iso()
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": spec.run_id,
        "study": spec.study_name,
        "hypothesis": spec.hypothesis_id,
        "condition": spec.condition_id,
        "scenario": spec.scenario,
        "seed": spec.seed,
        "execution_mode": spec.execution_mode,
        "started_at": started,
        "status": "pending",
        "resolved_overrides": spec.overrides,
        "command": None,
        "log_path": None,
        "run_dir": None,
        "evaluations": [],
        "reused": {
            "source": spec.reused_source,
            "eval": spec.reused_eval,
        },
        "lock": {
            "effective_config_sha256": None,
            "effective_config_path": None,
        },
    }

    if spec.execution_mode == "reuse_existing":
        reused_source = Path(spec.reused_source or "")
        if not reused_source.is_absolute():
            reused_source = (repo_root / reused_source).resolve()
        record["run_dir"] = str(reused_source)
        record["status"] = "reused"

        if spec.reused_eval:
            eval_path = Path(spec.reused_eval)
            if not eval_path.is_absolute():
                eval_path = (repo_root / eval_path).resolve()
            record["evaluations"].append(
                {
                    "id": "legacy_reused_eval",
                    "status": "reused",
                    "path": str(eval_path),
                    "command": [],
                    "log_path": None,
                    "return_code": None,
                    "tail": [],
                }
            )

        if spec.eval_specs and spec.re_evaluate:
            record["evaluations"] = _run_evaluations(
                spec,
                reused_source,
                repo_root,
                generated_dir,
                timeout_seconds,
            )

        record["finished_at"] = _now_iso()
        return record

    run_log = generated_dir / "logs" / f"{spec.run_id}.log"
    cmd = _build_run_command(spec)
    record["command"] = cmd
    record["log_path"] = str(run_log)

    rc, tail, run_dir = _run_subprocess(cmd, repo_root, run_log, timeout_seconds)
    record["return_code"] = rc
    record["tail"] = tail

    run_dir_path: Path | None = None
    if run_dir:
        run_dir_path = Path(run_dir)
        if not run_dir_path.is_absolute():
            run_dir_path = (repo_root / run_dir_path).resolve()

    if rc == 0 and run_dir_path is not None:
        record["status"] = "success"
        record["run_dir"] = str(run_dir_path)

        effective_cfg = run_dir_path / "effective_config.yaml"
        record["lock"]["effective_config_path"] = str(effective_cfg)
        record["lock"]["effective_config_sha256"] = _hash_file(effective_cfg)

        if spec.eval_specs:
            record["evaluations"] = _run_evaluations(
                spec,
                run_dir_path,
                repo_root,
                generated_dir,
                timeout_seconds,
            )
        else:
            record["evaluations"] = []
    else:
        record["status"] = "failed" if rc != PROCESS_TIMEOUT_RC else "timeout"

    record["finished_at"] = _now_iso()
    return record


def cmd_plan(args: argparse.Namespace) -> int:
    """Validate and expand a study file into a deterministic run plan."""
    study_path = Path(args.study).resolve()
    study_data = _load_yaml(study_path)
    run_specs, eval_specs, study = _expand_runs(study_path, study_data)

    print(f"Study: {study['name']}")
    print(f"Schema version: {SCHEMA_VERSION}")
    print(f"Global evaluators: {len(eval_specs)}")
    print(f"Total expanded runs: {len(run_specs)}")

    rows = _plan_rows(run_specs)
    if args.output:
        output = Path(args.output).resolve()
        _write_json(output, {"schema_version": SCHEMA_VERSION, "plan": rows})
        print(f"Wrote plan JSON: {output}")

    for row in rows[: min(len(rows), PLAN_PREVIEW_ROWS)]:
        print(
            "- "
            f"{row['run_id']} mode={row['mode']} scenario={row['scenario']} seed={row['seed']} evaluators={row['evaluators']}"
        )
    if len(rows) > PLAN_PREVIEW_ROWS:
        print(f"... and {len(rows) - PLAN_PREVIEW_ROWS} more")
    return 0


def cmd_generate_bash(args: argparse.Namespace) -> int:
    """Render all runnable study commands into a portable bash script."""
    study_path = Path(args.study).resolve()
    repo_root = Path(args.repo_root).resolve()
    study_data = _load_yaml(study_path)
    run_specs, _, study = _expand_runs(study_path, study_data)

    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    content = _render_bash_script(run_specs, repo_root)
    out.write_text(content, encoding="utf-8")
    os.chmod(out, 0o755)

    print(f"Generated bash script for study '{study['name']}': {out}")
    print(f"Commands: {sum(1 for s in run_specs if s.execution_mode == 'run')}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the expanded study plan and write reproducibility artifacts."""
    study_path = Path(args.study).resolve()
    repo_root = Path(args.repo_root).resolve()

    study_data = _load_yaml(study_path)
    run_specs, eval_specs, study = _expand_runs(study_path, study_data)

    generated_dir = repo_root / "experiments" / str(study["name"]) / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    lock_jsonl = generated_dir / "repro_lock.jsonl"
    lock_json = generated_dir / "repro_lock.json"
    enriched_yaml = generated_dir / "study_enriched.yaml"

    bash_out = generated_dir / "run_study.sh"
    bash_out.write_text(_render_bash_script(run_specs, repo_root), encoding="utf-8")
    os.chmod(bash_out, 0o755)

    max_concurrent = int(args.max_concurrent)
    timeout_seconds = int(args.timeout_seconds) if args.timeout_seconds > 0 else None

    print(f"Study: {study['name']}")
    print(f"Schema version: {SCHEMA_VERSION}")
    print(f"Expanded runs: {len(run_specs)}")
    print(f"Global evaluators: {[e.eval_id for e in eval_specs]}")
    print(f"Max concurrency: {max_concurrent}")
    print(f"Generated artifacts directory: {generated_dir}")

    if args.dry_run:
        rows = _plan_rows(run_specs)
        _write_json(generated_dir / "plan.json", {"schema_version": SCHEMA_VERSION, "plan": rows})
        print("Dry-run only. Wrote plan and bash script.")
        return 0

    records: list[dict[str, Any]] = []
    write_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        future_map = {
            pool.submit(
                _run_one_spec,
                spec,
                repo_root,
                generated_dir,
                timeout_seconds,
            ): spec
            for spec in run_specs
        }
        for future in as_completed(future_map):
            spec = future_map[future]
            try:
                record = future.result()
            except Exception as e:  # pragma: no cover
                record = {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": spec.run_id,
                    "study": spec.study_name,
                    "hypothesis": spec.hypothesis_id,
                    "condition": spec.condition_id,
                    "scenario": spec.scenario,
                    "seed": spec.seed,
                    "execution_mode": spec.execution_mode,
                    "status": "failed",
                    "error": str(e),
                    "finished_at": _now_iso(),
                }
            records.append(record)
            _write_jsonl_line(lock_jsonl, record, lock=write_lock)
            print(f"[{record.get('status', 'unknown'):>7}] {record.get('run_id')}")

    records.sort(key=lambda r: str(r.get("run_id", "")))
    _write_json(lock_json, {"schema_version": SCHEMA_VERSION, "records": records})
    _write_yaml(enriched_yaml, _enrich_study_with_results(study_data, records))

    success = sum(1 for r in records if r.get("status") in {"success", "reused"})
    failed = sum(1 for r in records if r.get("status") in {"failed", "timeout"})
    print("Run complete")
    print(f"Success/reused: {success}")
    print(f"Failed/timeout: {failed}")
    print(f"Repro lock JSONL: {lock_jsonl}")
    print(f"Repro lock JSON: {lock_json}")
    print(f"Enriched study YAML: {enriched_yaml}")

    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    """Build parser for planning, generation, and execution commands."""
    parser = argparse.ArgumentParser(description="Structured study runner for mastodon-sim")
    parser.add_argument("--study", required=True, help="Path to study YAML file")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root used as execution cwd (default: .)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Validate and expand study into concrete runs")
    p_plan.add_argument("--output", help="Optional output JSON path for expanded plan")
    p_plan.set_defaults(func=cmd_plan)

    p_bash = sub.add_parser("generate-bash", help="Generate runnable bash script")
    p_bash.add_argument("--output", required=True, help="Output path for generated bash script")
    p_bash.set_defaults(func=cmd_generate_bash)

    p_run = sub.add_parser("run", help="Execute study with optional eval hooks")
    p_run.add_argument("--max-concurrent", type=int, default=1, help="Maximum concurrent runs")
    p_run.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Per subprocess timeout (0 disables timeout)",
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not execute, only write generated plan and bash script",
    )
    p_run.set_defaults(func=cmd_run)

    return parser


def main() -> int:
    """Entry point for the study orchestration CLI."""
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except StudyConfigError as e:
        print(f"Study configuration error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
