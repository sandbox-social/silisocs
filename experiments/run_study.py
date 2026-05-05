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
import random
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

from experiments._internal.study_artifacts import (
    load_study_definition,
    organize_study_outputs,
    resolve_study_definition_path,
)

SCHEMA_VERSION = 1
DEFAULT_RUNNER_MODULE = "silisocs.runtime.runner"
PROCESS_TIMEOUT_RC = 124
PLAN_PREVIEW_ROWS = 10

BUILTIN_EVAL_PRESETS: dict[str, dict[str, Any]] = {
    "builtin.activity_summary": {
        "command": [
            "uv",
            "run",
            "python",
            "-m",
            "silisocs.evaluations.activity_summary",
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
            "-m",
            "silisocs.evaluations.activity_summary",
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
            "silisocs.evaluations.default_evaluators",
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
            "silisocs.evaluations.default_evaluators",
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
            "silisocs.evaluations.default_evaluators",
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
            "silisocs.evaluations.default_evaluators",
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
            "silisocs.evaluations.default_evaluators",
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
            "silisocs.evaluations.default_evaluators",
            "--mode",
            "probe_freetext",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_freetext_detailed.json",
    },
    # Study-specific eval.py — command uses ./eval.py resolved relative to the
    # study directory by _resolve_eval_spec. Requires experiments/studies/{name}/eval.py
    # to exist and accept --run-dir / --output args.
    "builtin.study_eval": {
        "command": ["uv", "run", "python", "./eval.py"],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "eval.json",
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
    study_id: str
    hypothesis_id: str
    condition_id: str
    sub_experiment: str
    scenario: str
    seed: int
    run_name: str
    execution_mode: str
    overrides: dict[str, Any]
    config_path: str | None
    runner_module: str
    re_evaluate: bool
    output_rootname: str | None = None
    command_override: tuple[str, ...] | None = None
    eval_specs: tuple[EvalSpec, ...] = ()
    reused_source: str | None = None
    reused_eval: str | None = None


def _resolve_study_id(study: dict[str, Any]) -> str:
    study_name = str(study.get("name", "")).strip()
    study_id = str(study.get("study_id", study_name)).strip()
    if not study_id:
        raise StudyConfigError("study.study_id (or study.name) must be a non-empty string")
    return study_id


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        return load_study_definition(path)
    except (FileNotFoundError, ValueError) as e:
        raise StudyConfigError(str(e)) from e


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
        seed_start = node.get(
            "seed_start", run_defaults.get("seed_start", run_defaults.get("seed", 1))
        )
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
            resolved = (study_root / token).resolve()
            if not resolved.exists():
                raise StudyConfigError(
                    f"{source}: script not found: {resolved} "
                    f"(required by preset '{preset or 'custom'}')"
                )
            resolved_cmd.append(str(resolved))
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


def _merge_eval_specs(
    base: tuple[EvalSpec, ...], extra: tuple[EvalSpec, ...], mode: str
) -> tuple[EvalSpec, ...]:
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


def _validate_schema(study_data: dict[str, Any]) -> None:  # noqa: C901, PLR0912, PLR0915
    schema_version = study_data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise StudyConfigError(
            f"Unsupported schema_version={schema_version}; expected {SCHEMA_VERSION}"
        )

    study = _ensure_mapping("study", study_data.get("study"))
    if not isinstance(study.get("name"), str) or not study["name"].strip():
        raise StudyConfigError("study.name is required and must be a non-empty string")
    _resolve_study_id(study)

    if "parent_studies" in study:
        _ensure_string_list("study.parent_studies", study.get("parent_studies"))

    derived = study.get("derived_from_runs")
    if derived is not None:
        if not isinstance(derived, list):
            raise StudyConfigError("study.derived_from_runs must be a list")
        for idx, item in enumerate(derived):
            if not isinstance(item, dict):
                raise StudyConfigError(f"study.derived_from_runs[{idx}] must be a mapping")
            source_study = str(item.get("source_study_id", "")).strip()
            run_id = str(item.get("run_id", "")).strip()
            run_path = str(item.get("run_path", "")).strip()
            if not source_study:
                raise StudyConfigError(
                    f"study.derived_from_runs[{idx}].source_study_id is required"
                )
            if not run_id and not run_path:
                raise StudyConfigError(
                    f"study.derived_from_runs[{idx}] must include run_id or run_path"
                )

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
                raise StudyConfigError(
                    f"hypotheses.{hyp_id}.conditions.{cond_id} must be a mapping"
                )
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
        # Always write a checkpoint every step so eval.py can compute action-type
        # metrics. Studies can override via run_defaults.overrides.
        {"sim.checkpoint.every_n_steps": 1},
        _extract_inline_overrides(run_defaults),
        _ensure_mapping("study.run_defaults.overrides", run_defaults.get("overrides")),
    )

    config_path = run_defaults.get("config_path")
    if config_path is not None and not isinstance(config_path, str):
        raise StudyConfigError("study.run_defaults.config_path must be a string")

    runner_module = str(run_defaults.get("runner_module", DEFAULT_RUNNER_MODULE))

    run_specs: list[RunSpec] = []
    study_name = str(study["name"]).strip()
    study_id = _resolve_study_id(study)
    default_run_name_template = str(
        run_defaults.get(
            "run_name_template",
            "{study_id}_{hypothesis_id}_{condition_id}_{scenario}_seed{seed}",
        )
    )
    default_output_root_override = run_defaults.get("output_root_override")
    if default_output_root_override is not None and not isinstance(
        default_output_root_override, str
    ):
        raise StudyConfigError("study.run_defaults.output_root_override must be a string")

    for hyp_id, hyp_node_any in hypotheses.items():
        hyp_node = _ensure_mapping(f"hypotheses.{hyp_id}", hyp_node_any)
        hyp_overrides = _ensure_mapping(f"hypotheses.{hyp_id}.overrides", hyp_node.get("overrides"))
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
            sub_experiment = str(cond_node.get("sub_experiment", cond_id)).strip() or cond_id
            scenarios = cond_scenarios or base_scenarios
            execution = _ensure_mapping(
                f"hypotheses.{hyp_id}.conditions.{cond_id}.execution",
                cond_node.get("execution"),
            )
            mode = str(execution.get("mode", "run"))
            re_evaluate = bool(execution.get("re_evaluate", False))

            condition_eval_specs = _resolve_condition_eval_specs(study_root, cond_node)
            eval_mode = str(cond_node.get("evaluation_mode", "append"))
            merged_eval_specs = _merge_eval_specs(
                global_eval_specs, condition_eval_specs, eval_mode
            )

            command_template: tuple[str, ...] | None = None
            if "command" in execution:
                command_template = _resolve_command_tokens(
                    execution.get("command"),
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.execution.command",
                )

            run_name_template = str(cond_node.get("run_name_template", default_run_name_template))
            cond_config_path = cond_node.get("config_path", config_path)
            if cond_config_path is not None and not isinstance(cond_config_path, str):
                raise StudyConfigError(
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.config_path must be a string"
                )
            output_root_override = cond_node.get(
                "output_root_override",
                execution.get("output_root_override", default_output_root_override),
            )
            if output_root_override is not None and not isinstance(output_root_override, str):
                raise StudyConfigError(
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.output_root_override must be a string"
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
                            study_id=study_id,
                            hypothesis_id=hyp_id,
                            condition_id=cond_id,
                            sub_experiment=sub_experiment,
                            scenario=scenario,
                            seed=seed,
                            run_name=f"{hyp_id}__{cond_id}__reuse_{idx}",
                            execution_mode=mode,
                            overrides={},
                            config_path=cond_config_path,
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
                    template_context = {
                        "run_id": run_id,
                        "study_name": study_name,
                        "study_id": study_id,
                        "hypothesis_id": hyp_id,
                        "condition_id": cond_id,
                        "sub_experiment": sub_experiment,
                        "scenario": scenario,
                        "seed": seed,
                    }
                    run_name = _format_template_token(run_name_template, template_context)
                    if output_root_override:
                        output_rootname = _format_template_token(
                            output_root_override, template_context
                        )
                    else:
                        output_rootname = (
                            f"experiments/studies/{study_id}/runs/"
                            f"{hyp_id}/{cond_id}/{scenario}/seed_{seed}/run"
                        )
                    command_override = None
                    if command_template is not None:
                        command_override = _format_command_template(
                            command_template,
                            template_context,
                        )

                    resolved_config_path = (
                        _format_template_token(cond_config_path, template_context)
                        if cond_config_path
                        else None
                    )

                    run_specs.append(
                        RunSpec(
                            run_id=run_id,
                            study_name=study_name,
                            study_id=study_id,
                            hypothesis_id=hyp_id,
                            condition_id=cond_id,
                            sub_experiment=sub_experiment,
                            scenario=scenario,
                            seed=seed,
                            run_name=run_name,
                            execution_mode=mode,
                            overrides=copy.deepcopy(merged),
                            config_path=resolved_config_path,
                            runner_module=runner_module,
                            re_evaluate=re_evaluate,
                            output_rootname=output_rootname,
                            command_override=command_override,
                            eval_specs=merged_eval_specs,
                        )
                    )

    return run_specs, global_eval_specs, study


def _build_run_command(spec: RunSpec) -> list[str]:
    if spec.command_override:
        return list(spec.command_override)

    cmd = [
        "uv",
        "run",
        "python",
        "-m",
        spec.runner_module,
    ]
    if spec.config_path:
        cmd.extend(["--config-path", spec.config_path])

    cmd.append(f"seed={spec.seed}")
    cmd.append(f"run_name={_normalize_override_value(spec.run_name)}")
    # output_rootname is set by the runner from Hydra's runtime.output_dir;
    # do not pass it as a CLI override.
    cmd.append(f"experiment_name={spec.study_name}")

    for key in sorted(spec.overrides):
        if key in {
            "seed",
            "run_name",
            "output_rootname",
            "experiment_name",
        }:
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
    extra_env: dict[str, str] | None = None,
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
            env=(dict(os.environ) | dict(extra_env)) if extra_env else None,
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


def _enrich_study_with_results(
    study_data: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
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
            "study_id": spec.study_id,
            "hypothesis": spec.hypothesis_id,
            "condition": spec.condition_id,
            "sub_experiment": spec.sub_experiment,
            "scenario": spec.scenario,
            "seed": spec.seed,
            "run_name": spec.run_name,
            "planned_output_rootname": spec.output_rootname,
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
    extra_env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for eval_spec in spec.eval_specs:
        eval_output = (
            generated_dir
            / "eval"
            / spec.hypothesis_id
            / spec.condition_id
            / spec.scenario
            / f"seed_{spec.seed}"
            / eval_spec.eval_id
            / eval_spec.output_subpath
        )
        eval_log = eval_output.with_suffix(eval_output.suffix + ".log")
        eval_context = {
            "run_id": spec.run_id,
            "study_name": spec.study_name,
            "study_id": spec.study_id,
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
        rc, tail, _ = _run_subprocess(
            cmd,
            repo_root,
            eval_log,
            timeout_seconds,
            extra_env=extra_env,
        )
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


def _run_one_spec(  # noqa: C901, PLR0912, PLR0915
    spec: RunSpec,
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
    assigned_gpu: str | None = None,
) -> dict[str, Any]:
    started = _now_iso()
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": spec.run_id,
        "study": spec.study_name,
        "hypothesis": spec.hypothesis_id,
        "condition": spec.condition_id,
        "sub_experiment": spec.sub_experiment,
        "scenario": spec.scenario,
        "seed": spec.seed,
        "execution_mode": spec.execution_mode,
        "started_at": started,
        "status": "pending",
        "resolved_overrides": spec.overrides,
        "study_id": spec.study_id,
        "run_name": spec.run_name,
        "planned_output_rootname": spec.output_rootname,
        "command": None,
        "log_path": None,
        "run_dir": None,
        "simulation_output_path": None,
        "evaluations": [],
        "eval_paths": {},
        "reused": {
            "source": spec.reused_source,
            "eval": spec.reused_eval,
        },
        "lock": {
            "effective_config_sha256": None,
            "effective_config_path": None,
        },
        "gpu_binding": assigned_gpu,
    }

    exec_env = {"CUDA_VISIBLE_DEVICES": assigned_gpu} if assigned_gpu else None

    if spec.execution_mode == "reuse_existing":
        reused_source = Path(spec.reused_source or "")
        if not reused_source.is_absolute():
            reused_source = (repo_root / reused_source).resolve()
        record["run_dir"] = str(reused_source)
        record["simulation_output_path"] = str(reused_source)
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
            record["eval_paths"]["legacy_reused_eval"] = str(eval_path)

        if spec.eval_specs and spec.re_evaluate:
            record["evaluations"] = _run_evaluations(
                spec,
                reused_source,
                repo_root,
                generated_dir,
                timeout_seconds,
                extra_env=exec_env,
            )
            record["eval_paths"] = {
                str(item.get("id")): str(item.get("path"))
                for item in record["evaluations"]
                if item.get("path")
            }

        record["finished_at"] = _now_iso()
        return record

    run_log = generated_dir / "logs" / f"{spec.run_id}.log"
    cmd = _build_run_command(spec)
    record["command"] = cmd
    record["log_path"] = str(run_log)

    rc, tail, run_dir = _run_subprocess(
        cmd,
        repo_root,
        run_log,
        timeout_seconds,
        extra_env=exec_env,
    )
    record["return_code"] = rc
    record["tail"] = tail

    run_dir_path: Path | None = None
    if run_dir:
        run_dir_path = Path(run_dir)
        if not run_dir_path.is_absolute():
            run_dir_path = (repo_root / run_dir_path).resolve()
    elif spec.output_rootname:
        fallback = Path(spec.output_rootname)
        if not fallback.is_absolute():
            fallback = (repo_root / fallback).resolve()
        if fallback.exists():
            run_dir_path = fallback

    if rc == 0 and run_dir_path is not None:
        record["status"] = "success"
        record["run_dir"] = str(run_dir_path)
        record["simulation_output_path"] = str(run_dir_path)

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
                extra_env=exec_env,
            )
            record["eval_paths"] = {
                str(item.get("id")): str(item.get("path"))
                for item in record["evaluations"]
                if item.get("path")
            }
        else:
            record["evaluations"] = []
    else:
        record["status"] = "failed" if rc != PROCESS_TIMEOUT_RC else "timeout"

    record["finished_at"] = _now_iso()
    return record


def _filter_run_specs(
    run_specs: list[RunSpec],
    only_hypothesis: str | None,
    only_condition: str | None,
    only_sub_experiment: str | None,
    only_seed: str | None,
) -> list[RunSpec]:
    filtered = run_specs
    if only_hypothesis:
        allowed = {part.strip() for part in only_hypothesis.split(",") if part.strip()}
        filtered = [spec for spec in filtered if spec.hypothesis_id in allowed]
    if only_condition:
        allowed = {part.strip() for part in only_condition.split(",") if part.strip()}
        filtered = [spec for spec in filtered if spec.condition_id in allowed]
    if only_sub_experiment:
        allowed = {part.strip() for part in only_sub_experiment.split(",") if part.strip()}
        filtered = [spec for spec in filtered if spec.sub_experiment in allowed]
    if only_seed:
        try:
            allowed = {int(part.strip()) for part in only_seed.split(",") if part.strip()}
        except ValueError as e:
            raise StudyConfigError("--only-seed must be a comma-separated list of integers") from e
        filtered = [spec for spec in filtered if spec.seed in allowed]
    return filtered


def _study_workspace_dir(repo_root: Path, study: dict[str, Any]) -> Path:
    study_id = _resolve_study_id(study)
    return repo_root / "experiments" / "studies" / study_id


def _study_generated_dir(repo_root: Path, study: dict[str, Any]) -> Path:
    return _study_workspace_dir(repo_root, study) / "generated"


def _write_study_index(
    path: Path, study_data: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    study = _ensure_mapping("study", study_data.get("study"))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "study": {
            "name": study.get("name"),
            "study_id": _resolve_study_id(study),
            "study_version": study.get("study_version"),
            "parent_studies": study.get("parent_studies", []),
            "derived_from_runs": study.get("derived_from_runs", []),
        },
        "records": records,
    }
    _write_json(path, payload)


def _resolve_summary_paths(repo_root: Path, study_data: dict[str, Any]) -> tuple[Path, Path]:
    study = _ensure_mapping("study", study_data.get("study"))
    study_id = _resolve_study_id(study)

    summary_md_raw = str(
        study.get("study_summary_path", f"experiments/studies/{study_id}/SUMMARY.md")
    )
    summary_log_raw = str(
        study.get(
            "summary_log_path",
            f"experiments/studies/{study_id}/generated/summary_log.jsonl",
        )
    )

    summary_md = Path(summary_md_raw)
    if not summary_md.is_absolute():
        summary_md = (repo_root / summary_md).resolve()

    summary_log = Path(summary_log_raw)
    if not summary_log.is_absolute():
        summary_log = (repo_root / summary_log).resolve()

    return summary_md, summary_log


def cmd_summary_append(args: argparse.Namespace) -> int:
    """Append a human/LLM study summary entry to JSONL and markdown files."""
    repo_root = Path(args.repo_root).resolve()
    study_path = Path(args.study).resolve()
    study_data = _load_yaml(study_path)

    summary_md, summary_log = _resolve_summary_paths(repo_root, study_data)
    study = _ensure_mapping("study", study_data.get("study"))
    entry = {
        "created_at": _now_iso(),
        "study_id": _resolve_study_id(study),
        "study_name": study.get("name"),
        "author": str(args.author),
        "hypothesis": args.hypothesis,
        "condition": args.condition,
        "note": str(args.note),
        "evidence_paths": list(args.evidence or []),
    }

    _write_jsonl_line(summary_log, entry)

    summary_md.parent.mkdir(parents=True, exist_ok=True)
    if not summary_md.exists():
        summary_md.write_text(
            f"# Study Summary\n\nStudy: {study.get('name')}\n\n", encoding="utf-8"
        )
    block = [
        "",
        f"## {entry['created_at']} | {entry['author']}",
        f"Hypothesis: {entry['hypothesis'] or 'n/a'}",
        f"Condition: {entry['condition'] or 'n/a'}",
        "",
        entry["note"],
    ]
    if entry["evidence_paths"]:
        block.append("")
        block.append("Evidence:")
        block.extend([f"- {item}" for item in entry["evidence_paths"]])

    with summary_md.open("a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")

    print(f"Appended summary log entry: {summary_log}")
    print(f"Updated markdown summary: {summary_md}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Validate and expand a study file into a deterministic run plan."""
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    study_data = _load_yaml(study_path)
    run_specs, eval_specs, study = _expand_runs(study_path, study_data)
    run_specs = _filter_run_specs(
        run_specs,
        args.only_hypothesis,
        args.only_condition,
        args.only_sub_experiment,
        args.only_seed,
    )

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
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    repo_root = Path(args.repo_root).resolve()
    study_data = _load_yaml(study_path)
    run_specs, _, study = _expand_runs(study_path, study_data)
    run_specs = _filter_run_specs(
        run_specs,
        args.only_hypothesis,
        args.only_condition,
        args.only_sub_experiment,
        args.only_seed,
    )

    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    content = _render_bash_script(run_specs, repo_root)
    out.write_text(content, encoding="utf-8")
    os.chmod(out, 0o755)

    print(f"Generated bash script for study '{study['name']}': {out}")
    print(f"Commands: {sum(1 for s in run_specs if s.execution_mode == 'run')}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:  # noqa: PLR0915
    """Execute the expanded study plan and write reproducibility artifacts."""
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    repo_root = Path(args.repo_root).resolve()

    study_data = _load_yaml(study_path)
    run_specs, eval_specs, study = _expand_runs(study_path, study_data)
    run_specs = _filter_run_specs(
        run_specs,
        args.only_hypothesis,
        args.only_condition,
        args.only_sub_experiment,
        args.only_seed,
    )

    generated_dir = _study_generated_dir(repo_root, study)
    generated_dir.mkdir(parents=True, exist_ok=True)

    lock_jsonl = generated_dir / "repro_lock.jsonl"
    lock_json = generated_dir / "repro_lock.json"
    study_index = generated_dir / "study_index.json"
    enriched_yaml = generated_dir / "study_enriched.yaml"

    bash_out = generated_dir / "run_study.sh"
    bash_out.write_text(_render_bash_script(run_specs, repo_root), encoding="utf-8")
    os.chmod(bash_out, 0o755)

    max_concurrent = int(args.max_concurrent)
    timeout_seconds = int(args.timeout_seconds) if args.timeout_seconds > 0 else None
    gpu_ids = _resolve_gpu_ids_for_run()

    gpu_bindings: dict[str, str] = {}
    if gpu_ids and max_concurrent > 1:
        shuffled = list(run_specs)
        random.shuffle(shuffled)
        for idx, spec in enumerate(shuffled):
            gpu_bindings[spec.run_id] = gpu_ids[idx % len(gpu_ids)]

    print(f"Study: {study['name']}")
    print(f"Schema version: {SCHEMA_VERSION}")
    print(f"Expanded runs: {len(run_specs)}")
    print(f"Global evaluators: {[e.eval_id for e in eval_specs]}")
    print(f"Max concurrency: {max_concurrent}")
    if gpu_bindings:
        print(
            "GPU distribution: enabled (randomized round-robin) "
            f"across CUDA_VISIBLE_DEVICES={','.join(gpu_ids)}"
        )
    elif gpu_ids:
        print(f"GPU distribution: single-worker mode; CUDA_VISIBLE_DEVICES={','.join(gpu_ids)}")
    else:
        print("GPU distribution: disabled (no RUN_STUDY_GPU_IDS/CUDA_VISIBLE_DEVICES set)")
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
                gpu_bindings.get(spec.run_id),
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
    _write_study_index(study_index, study_data, records)
    _write_yaml(enriched_yaml, _enrich_study_with_results(study_data, records))

    success = sum(1 for r in records if r.get("status") in {"success", "reused"})
    failed = sum(1 for r in records if r.get("status") in {"failed", "timeout"})
    print("Run complete")
    print(f"Success/reused: {success}")
    print(f"Failed/timeout: {failed}")
    print(f"Repro lock JSONL: {lock_jsonl}")
    print(f"Repro lock JSON: {lock_json}")
    print(f"Study index JSON: {study_index}")
    print(f"Enriched study YAML: {enriched_yaml}")

    organized_dir = organize_study_outputs(
        repo_root,
        study_data,
        records,
        dry_run=args.dry_run,
    )
    print(f"Organized study tree: {organized_dir}")

    return 1 if failed else 0


def cmd_organize(args: argparse.Namespace) -> int:
    """Rebuild organized study artifacts from existing reproducibility records."""
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    repo_root = Path(args.repo_root).resolve()

    study_data = _load_yaml(study_path)
    generated_dir = _study_generated_dir(
        repo_root, _ensure_mapping("study", study_data.get("study"))
    )
    lock_json = generated_dir / "repro_lock.json"

    if not lock_json.is_file():
        raise StudyConfigError(f"Missing repro_lock.json: {lock_json}")

    with lock_json.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    records = payload.get("records", [])
    if not isinstance(records, list):
        raise StudyConfigError(f"Invalid repro_lock.json records payload: {lock_json}")

    organized_dir = organize_study_outputs(
        repo_root,
        study_data,
        records,
        dry_run=args.dry_run,
        clean=not args.keep_existing,
    )
    print(f"Organized study tree: {organized_dir}")
    return 0


def _csv_compact(value: str | None) -> str:
    if not value:
        return ""
    return ",".join(part.strip() for part in value.split(",") if part.strip())


def _resolve_gpu_ids_for_run() -> list[str]:
    manual = os.environ.get("RUN_STUDY_GPU_IDS", "").strip()
    if manual:
        return [token.strip() for token in manual.split(",") if token.strip()]

    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
    if visible:
        return [token.strip() for token in visible.split(",") if token.strip()]

    return []


def _count_array_tasks(run_specs: list[RunSpec], array_mode: str) -> int:
    if array_mode == "run":
        return len(run_specs)
    if array_mode == "case":
        keys = {(spec.hypothesis_id, spec.condition_id) for spec in run_specs}
        return len(keys)
    if array_mode == "seed":
        keys = {(spec.hypothesis_id, spec.condition_id, spec.seed) for spec in run_specs}
        return len(keys)
    if array_mode == "hypothesis":
        keys = {spec.hypothesis_id for spec in run_specs}
        return len(keys)
    raise StudyConfigError(f"Unsupported array mode: {array_mode}")


def cmd_slurm_array(args: argparse.Namespace) -> int:
    """Generate (and optionally submit) a Slurm sbatch command from filtered study runs."""
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    repo_root = Path(args.repo_root).resolve()
    base_script = Path(args.base_script).resolve()

    if not base_script.is_file():
        raise StudyConfigError(f"Base script not found: {base_script}")

    study_data = _load_yaml(study_path)
    run_specs, _, _ = _expand_runs(study_path, study_data)
    run_specs = _filter_run_specs(
        run_specs,
        args.only_hypothesis,
        args.only_condition,
        args.only_sub_experiment,
        args.only_seed,
    )

    if not run_specs:
        print("No runs matched filters; nothing to submit.")
        return 0

    array_mode = str(args.array_mode).strip().lower()
    total_tasks = _count_array_tasks(run_specs, array_mode)
    array_spec = f"0-{total_tasks - 1}"

    study_rel = os.path.relpath(study_path, repo_root)
    plan_json = (
        repo_root / "logs" / f"study_plan_{Path(study_rel).stem}_{array_mode}_{_now_iso()}.json"
    )
    plan_json.parent.mkdir(parents=True, exist_ok=True)
    _write_json(plan_json, {"schema_version": SCHEMA_VERSION, "plan": _plan_rows(run_specs)})

    export_parts = {
        "REPO_ROOT": str(repo_root),
        "UV_HOME": str(Path(args.uv_home).resolve()),
        "STUDY_FILE": study_rel,
        "PLAN_JSON": str(plan_json),
        "ARRAY_MODE": array_mode,
        "HYPOTHESIS_IDS": _csv_compact(args.only_hypothesis),
        "CONDITION_IDS": _csv_compact(args.only_condition),
        "SUB_EXPERIMENT_IDS": _csv_compact(args.only_sub_experiment),
        "SEED_IDS": _csv_compact(args.only_seed),
        "MAX_CONCURRENT": str(int(args.max_concurrent)),
    }
    export_arg = ",".join(f"{k}={v}" for k, v in export_parts.items())

    sbatch_cmd = [
        "sbatch",
        f"--array={array_spec}",
        f"--export={export_arg}",
        str(base_script),
    ]

    print(f"Array mode: {array_mode}")
    print(f"Matched expanded runs: {len(run_specs)}")
    print(f"Array tasks: {total_tasks}")
    print(f"Array spec: {array_spec}")
    print(f"Plan JSON: {plan_json}")
    print("Prepared sbatch command:")
    print(" ".join(shlex.quote(part) for part in sbatch_cmd))

    if not args.submit:
        print("Dry mode: add --submit to dispatch the job.")
        return 0

    result = subprocess.run(
        sbatch_cmd,
        cwd=str(repo_root),
        check=False,
        text=True,
        capture_output=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
    return int(result.returncode)


def build_parser() -> argparse.ArgumentParser:
    """Build parser for planning, generation, and execution commands."""
    parser = argparse.ArgumentParser(description="Structured study runner for mastodon-sim")
    parser.add_argument(
        "--study",
        required=True,
        help="Path to a study directory or study.yaml file",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root used as execution cwd (default: .)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Validate and expand study into concrete runs")
    p_plan.add_argument("--output", help="Optional output JSON path for expanded plan")
    p_plan.add_argument(
        "--only-hypothesis",
        default=None,
        help="Optional comma-separated hypothesis IDs to include",
    )
    p_plan.add_argument(
        "--only-condition",
        default=None,
        help="Optional comma-separated condition IDs to include",
    )
    p_plan.add_argument(
        "--only-sub-experiment",
        default=None,
        help="Optional comma-separated sub_experiment labels to include",
    )
    p_plan.add_argument(
        "--only-seed",
        default=None,
        help="Optional comma-separated seed values to include",
    )
    p_plan.set_defaults(func=cmd_plan)

    p_bash = sub.add_parser("generate-bash", help="Generate runnable bash script")
    p_bash.add_argument("--output", required=True, help="Output path for generated bash script")
    p_bash.add_argument(
        "--only-hypothesis",
        default=None,
        help="Optional comma-separated hypothesis IDs to include",
    )
    p_bash.add_argument(
        "--only-condition",
        default=None,
        help="Optional comma-separated condition IDs to include",
    )
    p_bash.add_argument(
        "--only-sub-experiment",
        default=None,
        help="Optional comma-separated sub_experiment labels to include",
    )
    p_bash.add_argument(
        "--only-seed",
        default=None,
        help="Optional comma-separated seed values to include",
    )
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
    p_run.add_argument(
        "--only-hypothesis",
        default=None,
        help="Optional comma-separated hypothesis IDs to include",
    )
    p_run.add_argument(
        "--only-condition",
        default=None,
        help="Optional comma-separated condition IDs to include",
    )
    p_run.add_argument(
        "--only-sub-experiment",
        default=None,
        help="Optional comma-separated sub_experiment labels to include",
    )
    p_run.add_argument(
        "--only-seed",
        default=None,
        help="Optional comma-separated seed values to include",
    )
    p_run.set_defaults(func=cmd_run)

    p_org = sub.add_parser("organize", help="Rebuild organized study artifacts")
    p_org.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files, only validate and resolve paths",
    )
    p_org.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not clean the organized output directory before rebuilding",
    )
    p_org.set_defaults(func=cmd_organize)

    p_slurm = sub.add_parser(
        "slurm-array",
        help="Build Slurm sbatch command from study filters (optionally submit)",
    )
    p_slurm.add_argument(
        "--base-script",
        required=True,
        help="Base Slurm script path (required)",
    )
    p_slurm.add_argument(
        "--array-mode",
        choices=["case", "seed", "hypothesis", "run"],
        default="case",
        help="Array granularity: case(default), seed, hypothesis, or run",
    )
    p_slurm.add_argument(
        "--max-concurrent",
        type=int,
        default=8,
        help="run_study --max-concurrent passed to each array task",
    )
    p_slurm.add_argument(
        "--uv-home",
        default=str(Path.home()),
        help="Path where uv env with vLLM is available (default: $HOME)",
    )
    p_slurm.add_argument(
        "--only-hypothesis",
        default=None,
        help="Optional comma-separated hypothesis IDs to include",
    )
    p_slurm.add_argument(
        "--only-condition",
        default=None,
        help="Optional comma-separated condition IDs to include",
    )
    p_slurm.add_argument(
        "--only-sub-experiment",
        default=None,
        help="Optional comma-separated sub_experiment labels to include",
    )
    p_slurm.add_argument(
        "--only-seed",
        default=None,
        help="Optional comma-separated seed values to include",
    )
    p_slurm.add_argument(
        "--submit",
        action="store_true",
        help="Submit to Slurm via sbatch. Otherwise prints command only.",
    )
    p_slurm.set_defaults(func=cmd_slurm_array)

    p_summary = sub.add_parser("summary-append", help="Append a study summary entry")
    p_summary.add_argument("--author", required=True, help="Author label for this summary entry")
    p_summary.add_argument("--note", required=True, help="Summary note text")
    p_summary.add_argument("--hypothesis", default=None, help="Optional hypothesis ID")
    p_summary.add_argument("--condition", default=None, help="Optional condition ID")
    p_summary.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="Evidence path; can be provided multiple times",
    )
    p_summary.set_defaults(func=cmd_summary_append)

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
