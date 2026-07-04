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
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

from silisocs.runtime.checkpointing import resolve_checkpoint_source
from silisocs.studies.study_artifacts import (
    load_study_definition,
    organize_study_outputs,
    resolve_study_definition_path,
)
from silisocs.studies.study_schema import validate_schema
from silisocs.studies.study_types import (
    SCHEMA_VERSION,
    EvalSpec,
    RunSpec,
    StudyConfigError,
    ensure_mapping,
    ensure_string_list,
    format_command_template,
    format_template_token,
    now_iso,
    resolve_command_tokens,
    resolve_study_id,
)

DEFAULT_RUNNER_MODULE = "silisocs.runtime.runner"
PROCESS_TIMEOUT_RC = 124
PLAN_PREVIEW_ROWS = 10
PREFLIGHT_CONFIRM_RUN_COUNT = 50
RUN_COMPLETE_MARKER = "RUN_COMPLETE.json"

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


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        return load_study_definition(path)
    except (FileNotFoundError, ValueError) as e:
        raise StudyConfigError(str(e)) from e


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=False)


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _environment_provenance(repo_root: Path) -> dict[str, Any]:
    """Capture the execution environment so a study run can be reproduced.

    Config SHAs alone cannot reproduce a run: results depend on the code
    revision and dependency set that executed it.
    """

    def _git(*git_args: str) -> str | None:
        try:
            proc = subprocess.run(
                ["git", *git_args],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    try:
        silisocs_version = importlib.metadata.version("silisocs")
    except importlib.metadata.PackageNotFoundError:
        silisocs_version = None

    dirty_output = _git("status", "--porcelain")
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(dirty_output) if dirty_output is not None else None,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "silisocs_version": silisocs_version,
        "uv_lock_sha256": _hash_file(repo_root / "uv.lock"),
        "captured_at": now_iso(),
    }


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
        "checkpoint_every_n_steps",
    }
    out: dict[str, Any] = {}
    for key, value in run_defaults.items():
        if key in known:
            continue
        if "." in key:
            out[key] = value
    return out


def _checkpoint_cadence_overrides(run_defaults: dict[str, Any]) -> dict[str, Any]:
    """Resolve the checkpoint cadence override injected into every run.

    Defaults to 1 (a checkpoint every step) so eval.py can read the final
    checkpoint for action-type metrics. Setting
    run_defaults.checkpoint_every_n_steps to another positive int changes the
    cadence; setting it to null/0/false disables the injection entirely.
    """
    cadence = run_defaults.get("checkpoint_every_n_steps", 1)
    if cadence is None or cadence is False or cadence == 0:
        return {}
    if isinstance(cadence, bool) or not isinstance(cadence, int) or cadence < 0:
        raise StudyConfigError(
            "run_defaults.checkpoint_every_n_steps must be a positive int, "
            "or null/0/false to disable checkpoint injection"
        )
    return {"sim.checkpoint.every_n_steps": cadence}


def _resolve_scenarios(study: dict[str, Any], run_defaults: dict[str, Any]) -> list[str]:
    scenarios = ensure_string_list("study.scenarios", study.get("scenarios"))
    if not scenarios:
        scenarios = ensure_string_list("study.base_scenarios", study.get("base_scenarios"))
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

    command = resolve_command_tokens(eval_item.get("command"), f"{source}.command")
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
    explicit_cfg = ensure_mapping(f"{source}.explicit_args", eval_item.get("explicit_args"))
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
        replacement_specs: dict[str, EvalSpec] = {spec.eval_id: spec for spec in extra}
        return tuple(replacement_specs.values())

    merged_specs: dict[str, EvalSpec] = {spec.eval_id: spec for spec in base}
    for spec in extra:
        merged_specs[spec.eval_id] = spec
    return tuple(merged_specs.values())


def _resolve_eval_specs(study_root: Path, study_data: dict[str, Any]) -> tuple[EvalSpec, ...]:
    raw_items: list[dict[str, Any]] = []

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


def _expand_runs(  # noqa: C901, PLR0912, PLR0915
    study_path: Path, study_data: dict[str, Any]
) -> tuple[list[RunSpec], tuple[EvalSpec, ...], dict[str, Any]]:
    validate_schema(study_data)

    study_root = study_path.parent
    study = ensure_mapping("study", study_data["study"])
    hypotheses = ensure_mapping("hypotheses", study_data["hypotheses"])
    run_defaults = ensure_mapping("study.run_defaults", study.get("run_defaults"))
    base_scenarios = _resolve_scenarios(study, run_defaults)

    global_eval_specs = _resolve_eval_specs(study_root, study_data)

    default_overrides = _merge_overrides(
        # Checkpoint cadence injected so eval.py can compute action-type metrics.
        # Controlled by run_defaults.checkpoint_every_n_steps (default: 1);
        # studies can also override via run_defaults.overrides.
        _checkpoint_cadence_overrides(run_defaults),
        _extract_inline_overrides(run_defaults),
        ensure_mapping("study.run_defaults.overrides", run_defaults.get("overrides")),
    )

    config_path = run_defaults.get("config_path")
    if config_path is not None and not isinstance(config_path, str):
        raise StudyConfigError("study.run_defaults.config_path must be a string")

    runner_module = str(run_defaults.get("runner_module", DEFAULT_RUNNER_MODULE))

    run_specs: list[RunSpec] = []
    study_name = str(study["name"]).strip()
    study_id = resolve_study_id(study)
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
        hyp_node = ensure_mapping(f"hypotheses.{hyp_id}", hyp_node_any)
        hyp_overrides = ensure_mapping(f"hypotheses.{hyp_id}.overrides", hyp_node.get("overrides"))
        cond_map = ensure_mapping(
            f"hypotheses.{hyp_id}.conditions",
            hyp_node.get("conditions"),
        )

        for cond_id, cond_node_any in cond_map.items():
            cond_node = ensure_mapping(f"hypotheses.{hyp_id}.conditions.{cond_id}", cond_node_any)
            cond_overrides = ensure_mapping(
                f"hypotheses.{hyp_id}.conditions.{cond_id}.overrides",
                cond_node.get("overrides"),
            )
            cond_scenarios = ensure_string_list(
                f"hypotheses.{hyp_id}.conditions.{cond_id}.scenarios",
                cond_node.get("scenarios"),
            )
            sub_experiment = str(cond_node.get("sub_experiment", cond_id)).strip() or cond_id
            scenarios = cond_scenarios or base_scenarios
            execution = ensure_mapping(
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
                command_template = resolve_command_tokens(
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
                reuse = ensure_mapping(
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
                    run_name = format_template_token(run_name_template, template_context)
                    if output_root_override:
                        output_rootname = format_template_token(
                            output_root_override, template_context
                        )
                    else:
                        output_rootname = (
                            f"experiments/studies/{study_id}/runs/"
                            f"{hyp_id}/{cond_id}/{scenario}/seed_{seed}/run"
                        )
                    command_override = None
                    if command_template is not None:
                        command_override = format_command_template(
                            command_template,
                            template_context,
                        )

                    resolved_config_path = (
                        format_template_token(cond_config_path, template_context)
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

    # Default to the interpreter running this study (it already has silisocs
    # importable), which is correct for both `uv run` repo workflows and
    # pip-installed venvs. RUN_STUDY_PYTHON overrides it for explicit control.
    runner_python = os.environ.get("RUN_STUDY_PYTHON", "").strip() or sys.executable
    cmd = [runner_python, "-m", spec.runner_module]
    if spec.config_path:
        cmd.extend(["--config-path", spec.config_path])

    cmd.append(f"seed={spec.seed}")
    cmd.append(f"run_name={_normalize_override_value(spec.run_name)}")
    if spec.output_rootname:
        cmd.append(f"output_rootname={_normalize_override_value(spec.output_rootname)}")
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
    # Defer to the canonical runtime resolver so study eval picks the exact same
    # checkpoint the runtime restore would (highest parsed step, step_*_checkpoint.json
    # only). It raises FileNotFoundError / ValueError for no-usable / unparseable
    # checkpoints; translate both to this function's "None" contract.
    try:
        return resolve_checkpoint_source(run_dir)
    except (FileNotFoundError, ValueError):
        return None


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

        # Drain stdout on a separate thread so the timeout is enforced via
        # proc.wait(timeout=...) below even when the child produces NO output: a
        # blocking `for line in proc.stdout` only returns at EOF, so a hang that
        # holds stdout open (deadlock, stuck network/LLM call) would otherwise
        # never reach the timeout and run forever.
        def _drain_stdout() -> None:
            nonlocal parsed_output_dir
            assert proc.stdout is not None
            for line in proc.stdout:
                log_file.write(line)
                output_tail.append(line.rstrip("\n"))
                out_dir = _extract_output_dir_from_line(line)
                if out_dir:
                    parsed_output_dir = out_dir

        reader = threading.Thread(target=_drain_stdout, daemon=True)
        reader.start()
        try:
            return_code = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            output_tail.append(f"TIMEOUT after {timeout_seconds}s")
            return_code = PROCESS_TIMEOUT_RC
        # Let the drain thread observe EOF (the pipe closes once the process
        # exits / is killed) and flush remaining output before the log closes.
        reader.join(timeout=5)

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
        "generated_at": now_iso(),
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
    cmd = list(format_command_template(eval_spec.command, context))
    cmd.extend(format_command_template(eval_spec.static_args, context))

    if not eval_spec.manage_io_args:
        return cmd

    if eval_spec.input_mode == "run_dir":
        cmd.extend([eval_spec.run_dir_arg, str(run_dir)])
    else:
        checkpoint = _find_latest_checkpoint(run_dir)
        explicit_map: dict[str, Path | None] = {
            "checkpoint": checkpoint,
            "effective_config": run_dir / "effective_config.yaml",
            "metrics": run_dir / "sim_metrics.json",
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


def _build_eval_paths(evaluations: list[dict[str, Any]]) -> dict[str, str]:
    """Map evaluation id -> output path for evaluations that produced one."""
    return {str(item.get("id")): str(item.get("path")) for item in evaluations if item.get("path")}


def _initialize_run_record(spec: RunSpec, assigned_gpu: str | None, started: str) -> dict[str, Any]:
    """Build the base run record shared by the reuse and new-run paths."""
    return {
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


def _run_reused_spec(
    spec: RunSpec,
    record: dict[str, Any],
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
    exec_env: dict[str, str] | None,
) -> dict[str, Any]:
    """Resolve (and optionally re-evaluate) an existing run referenced by the spec."""
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
        record["eval_paths"] = _build_eval_paths(record["evaluations"])

    record["finished_at"] = now_iso()
    return record


def _run_new_spec(
    spec: RunSpec,
    record: dict[str, Any],
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
    exec_env: dict[str, str] | None,
) -> dict[str, Any]:
    """Execute a fresh simulation run, resolve its output dir, and run evaluations."""
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
            record["eval_paths"] = _build_eval_paths(record["evaluations"])
        else:
            record["evaluations"] = []

        # Idempotent-resume marker: a later `run` invocation skips this run
        # unless --force is given.
        _write_json(
            run_dir_path / RUN_COMPLETE_MARKER,
            {
                "run_id": spec.run_id,
                "finished_at": now_iso(),
                "effective_config_sha256": record["lock"]["effective_config_sha256"],
                "return_code": rc,
            },
        )
    else:
        record["status"] = "failed" if rc != PROCESS_TIMEOUT_RC else "timeout"

    record["finished_at"] = now_iso()
    return record


def _run_one_spec(
    spec: RunSpec,
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
    assigned_gpu: str | None = None,
) -> dict[str, Any]:
    record = _initialize_run_record(spec, assigned_gpu, now_iso())
    exec_env = {"CUDA_VISIBLE_DEVICES": assigned_gpu} if assigned_gpu else None
    if spec.execution_mode == "reuse_existing":
        return _run_reused_spec(spec, record, repo_root, generated_dir, timeout_seconds, exec_env)
    return _run_new_spec(spec, record, repo_root, generated_dir, timeout_seconds, exec_env)


def _planned_run_dir(spec: RunSpec, repo_root: Path) -> Path | None:
    """Resolve the planned output directory the same way _run_one_spec does."""
    if not spec.output_rootname:
        return None
    planned = Path(spec.output_rootname)
    if not planned.is_absolute():
        planned = (repo_root / planned).resolve()
    return planned


def _load_complete_marker(run_dir: Path) -> dict[str, Any] | None:
    marker_path = run_dir / RUN_COMPLETE_MARKER
    if not marker_path.is_file():
        return None
    try:
        with marker_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _skipped_complete_record(spec: RunSpec, run_dir: Path, generated_dir: Path) -> dict[str, Any]:
    """Build a repro record for a run skipped because RUN_COMPLETE.json exists."""
    now = now_iso()
    effective_cfg = run_dir / "effective_config.yaml"
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
        "started_at": now,
        "finished_at": now,
        "status": "skipped_complete",
        "resolved_overrides": spec.overrides,
        "study_id": spec.study_id,
        "run_name": spec.run_name,
        "planned_output_rootname": spec.output_rootname,
        "command": None,
        "log_path": None,
        "run_dir": str(run_dir),
        "simulation_output_path": str(run_dir),
        "evaluations": [],
        "eval_paths": {},
        "reused": {"source": None, "eval": None},
        "lock": {
            "effective_config_sha256": _hash_file(effective_cfg),
            "effective_config_path": str(effective_cfg),
        },
        "gpu_binding": None,
        "complete_marker": _load_complete_marker(run_dir),
    }

    # Relink prior evaluator outputs (deterministic paths) so the organized
    # tree keeps eval data for skipped runs.
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
        if not eval_output.is_file():
            continue
        record["evaluations"].append(
            {
                "id": eval_spec.eval_id,
                "status": "reused",
                "path": str(eval_output),
                "command": [],
                "log_path": None,
                "return_code": None,
                "tail": [],
            }
        )
        record["eval_paths"][eval_spec.eval_id] = str(eval_output)

    return record


def _partition_completed_runs(
    run_specs: list[RunSpec],
    repo_root: Path,
    generated_dir: Path,
    force: bool,
) -> tuple[list[RunSpec], list[dict[str, Any]]]:
    """Split specs into pending runs and records for already-complete runs."""
    if force:
        return list(run_specs), []

    pending: list[RunSpec] = []
    skipped: list[dict[str, Any]] = []
    for spec in run_specs:
        run_dir = _planned_run_dir(spec, repo_root)
        if (
            spec.execution_mode == "run"
            and run_dir is not None
            and (run_dir / RUN_COMPLETE_MARKER).is_file()
        ):
            skipped.append(_skipped_complete_record(spec, run_dir, generated_dir))
        else:
            pending.append(spec)
    return pending, skipped


def _spec_scale(spec: RunSpec) -> tuple[int | None, int | None]:
    """Extract (num_agents, num_steps) from resolved overrides when derivable."""

    def _as_int(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    return _as_int(spec.overrides.get("num_agents")), _as_int(spec.overrides.get("num_steps"))


def _preflight_summary(run_specs: list[RunSpec]) -> list[str]:
    """Render the cost/scale preflight lines for a set of planned runs."""
    lines = [f"Preflight: {len(run_specs)} run(s) planned"]
    total_agent_steps = 0
    unknown = 0
    for spec in run_specs:
        agents, steps = _spec_scale(spec)
        if agents is not None and steps is not None:
            total_agent_steps += agents * steps
        else:
            unknown += 1
    for spec in run_specs[:PLAN_PREVIEW_ROWS]:
        agents, steps = _spec_scale(spec)
        lines.append(
            f"  - {spec.run_id}: "
            f"num_agents={agents if agents is not None else '?'} "
            f"num_steps={steps if steps is not None else '?'}"
        )
    if len(run_specs) > PLAN_PREVIEW_ROWS:
        lines.append(f"  ... and {len(run_specs) - PLAN_PREVIEW_ROWS} more")
    if unknown == len(run_specs) and run_specs:
        lines.append(f"Estimated total agent-steps: ? ({unknown} run(s) with unknown scale)")
    elif unknown:
        lines.append(
            f"Estimated total agent-steps: >= {total_agent_steps} "
            f"({unknown} run(s) with unknown scale)"
        )
    else:
        lines.append(f"Estimated total agent-steps: {total_agent_steps}")
    return lines


def _confirm_run_count(run_count: int, assume_yes: bool) -> bool:
    """Gate large launches behind --yes or an interactive confirmation."""
    if run_count <= PREFLIGHT_CONFIRM_RUN_COUNT or assume_yes:
        return True
    if not sys.stdin.isatty():
        print(
            f"Aborting: {run_count} runs exceed the preflight threshold "
            f"({PREFLIGHT_CONFIRM_RUN_COUNT}) and stdin is not a TTY. "
            "Re-run with --yes to proceed.",
            file=sys.stderr,
        )
        return False
    answer = input(
        f"About to launch {run_count} runs "
        f"(threshold: {PREFLIGHT_CONFIRM_RUN_COUNT}). Continue? [y/N] "
    )
    return answer.strip().lower() in {"y", "yes"}


def _filter_run_specs(
    run_specs: list[RunSpec],
    only_hypothesis: str | None,
    only_condition: str | None,
    only_sub_experiment: str | None,
    only_seed: str | None,
    only_run_id: str | None = None,
) -> list[RunSpec]:
    filtered = run_specs
    if only_run_id:
        allowed = {part.strip() for part in only_run_id.split(",") if part.strip()}
        filtered = [spec for spec in filtered if spec.run_id in allowed]
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
            allowed_seeds = {int(part.strip()) for part in only_seed.split(",") if part.strip()}
        except ValueError as e:
            raise StudyConfigError("--only-seed must be a comma-separated list of integers") from e
        filtered = [spec for spec in filtered if spec.seed in allowed_seeds]
    return filtered


def _study_workspace_dir(repo_root: Path, study: dict[str, Any]) -> Path:
    study_id = resolve_study_id(study)
    return repo_root / "experiments" / "studies" / study_id


def _study_generated_dir(repo_root: Path, study: dict[str, Any]) -> Path:
    return _study_workspace_dir(repo_root, study) / "generated"


def _write_study_index(
    path: Path, study_data: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    study = ensure_mapping("study", study_data.get("study"))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "study": {
            "name": study.get("name"),
            "study_id": resolve_study_id(study),
            "study_version": study.get("study_version"),
            "parent_studies": study.get("parent_studies", []),
            "derived_from_runs": study.get("derived_from_runs", []),
        },
        "records": records,
    }
    _write_json(path, payload)


def _resolve_summary_paths(repo_root: Path, study_data: dict[str, Any]) -> tuple[Path, Path]:
    study = ensure_mapping("study", study_data.get("study"))
    study_id = resolve_study_id(study)

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
    study = ensure_mapping("study", study_data.get("study"))
    entry = {
        "created_at": now_iso(),
        "study_id": resolve_study_id(study),
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
        getattr(args, "only_run_id", None),
    )

    print(f"Study: {study['name']}")
    print(f"Schema version: {SCHEMA_VERSION}")
    print(f"Global evaluators: {len(eval_specs)}")
    print(f"Total expanded runs: {len(run_specs)}")
    for line in _preflight_summary(run_specs):
        print(line)

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
        getattr(args, "only_run_id", None),
    )

    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    content = _render_bash_script(run_specs, repo_root)
    out.write_text(content, encoding="utf-8")
    os.chmod(out, 0o755)

    print(f"Generated bash script for study '{study['name']}': {out}")
    print(f"Commands: {sum(1 for s in run_specs if s.execution_mode == 'run')}")
    return 0


def _execute_pending_runs(
    pending_specs: list[RunSpec],
    skipped_records: list[dict[str, Any]],
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
    max_concurrent: int,
    gpu_bindings: dict[str, str],
    lock_jsonl: Path,
) -> list[dict[str, Any]]:
    """Run the pending specs concurrently, streaming each record to the lock JSONL.

    Already-complete (skipped) records are emitted first, then each pending spec is
    run via ``_run_one_spec`` in a bounded thread pool; a worker exception is turned
    into a failed record rather than aborting the study.
    """
    records: list[dict[str, Any]] = []
    write_lock = threading.Lock()

    for record in skipped_records:
        records.append(record)
        _write_jsonl_line(lock_jsonl, record, lock=write_lock)
        print(f"[{record.get('status', 'unknown'):>7}] {record.get('run_id')}")

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
            for spec in pending_specs
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
                    "finished_at": now_iso(),
                }
            records.append(record)
            _write_jsonl_line(lock_jsonl, record, lock=write_lock)
            print(f"[{record.get('status', 'unknown'):>7}] {record.get('run_id')}")

    return records


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
        getattr(args, "only_run_id", None),
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

    pending_specs, skipped_records = _partition_completed_runs(
        run_specs, repo_root, generated_dir, force=bool(args.force)
    )

    gpu_bindings: dict[str, str] = {}
    if gpu_ids and max_concurrent > 1:
        shuffled = list(pending_specs)
        random.shuffle(shuffled)
        for idx, spec in enumerate(shuffled):
            gpu_bindings[spec.run_id] = gpu_ids[idx % len(gpu_ids)]

    provenance = _environment_provenance(repo_root)
    print(f"Study: {study['name']}")
    print(f"Schema version: {SCHEMA_VERSION}")
    print(f"Git commit: {provenance.get('git_commit') or 'unknown'}")
    if provenance.get("git_dirty"):
        print("⚠ Working tree has uncommitted changes; repro lock records git_dirty=true")
    print(f"Expanded runs: {len(run_specs)}")
    if skipped_records:
        print(f"Already complete (will skip): {len(skipped_records)}")
    for line in _preflight_summary(pending_specs):
        print(line)
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

    if not _confirm_run_count(len(pending_specs), bool(args.yes)):
        return 2

    records = _execute_pending_runs(
        pending_specs,
        skipped_records,
        repo_root,
        generated_dir,
        timeout_seconds,
        max_concurrent,
        gpu_bindings,
        lock_jsonl,
    )

    records.sort(key=lambda r: str(r.get("run_id", "")))
    _write_json(
        lock_json,
        {
            "schema_version": SCHEMA_VERSION,
            "environment": provenance,
            "records": records,
        },
    )
    _write_study_index(study_index, study_data, records)
    _write_yaml(enriched_yaml, _enrich_study_with_results(study_data, records))

    success = sum(
        1 for r in records if r.get("status") in {"success", "reused", "skipped_complete"}
    )
    failed = sum(1 for r in records if r.get("status") in {"failed", "timeout"})
    print("Run complete")
    print(f"Success/reused: {success}")
    print(f"Failed/timeout: {failed}")
    print(f"Skipped {len(skipped_records)} already-complete runs (use --force to re-run)")
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
        repo_root, ensure_mapping("study", study_data.get("study"))
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
        case_keys = {(spec.hypothesis_id, spec.condition_id) for spec in run_specs}
        return len(case_keys)
    if array_mode == "seed":
        seed_keys = {(spec.hypothesis_id, spec.condition_id, spec.seed) for spec in run_specs}
        return len(seed_keys)
    if array_mode == "hypothesis":
        hypothesis_keys = {spec.hypothesis_id for spec in run_specs}
        return len(hypothesis_keys)
    raise StudyConfigError(f"Unsupported array mode: {array_mode}")


def _submitit_group_filters(run_specs: list[RunSpec], array_mode: str) -> list[dict[str, str]]:
    """Return study-runner filters for each submitted group."""
    groups: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    if array_mode == "run":
        return [{"only_run_id": spec.run_id} for spec in run_specs]
    if array_mode == "case":
        for spec in run_specs:
            case_key = (spec.hypothesis_id, spec.condition_id)
            if case_key not in seen:
                seen.add(case_key)
                groups.append({"only_hypothesis": case_key[0], "only_condition": case_key[1]})
        return groups
    if array_mode == "seed":
        for spec in run_specs:
            seed_key = (spec.hypothesis_id, spec.condition_id, str(spec.seed))
            if seed_key not in seen:
                seen.add(seed_key)
                groups.append(
                    {
                        "only_hypothesis": seed_key[0],
                        "only_condition": seed_key[1],
                        "only_seed": seed_key[2],
                    }
                )
        return groups
    if array_mode == "hypothesis":
        for spec in run_specs:
            hypothesis_key = (spec.hypothesis_id,)
            if hypothesis_key not in seen:
                seen.add(hypothesis_key)
                groups.append({"only_hypothesis": hypothesis_key[0]})
        return groups
    raise StudyConfigError(f"Unsupported array mode: {array_mode}")


def _filter_args_from_mapping(filters: dict[str, str]) -> list[str]:
    options = {
        "only_run_id": "--only-run-id",
        "only_hypothesis": "--only-hypothesis",
        "only_condition": "--only-condition",
        "only_sub_experiment": "--only-sub-experiment",
        "only_seed": "--only-seed",
    }
    out: list[str] = []
    for key, option in options.items():
        value = filters.get(key, "")
        if value:
            out.extend([option, value])
    return out


def _build_submitit_job_commands(
    *,
    study_path: Path,
    repo_root: Path,
    groups: list[dict[str, str]],
    max_concurrent: int,
    timeout_seconds: int,
) -> list[list[str]]:
    study_arg = os.path.relpath(study_path, repo_root)
    commands: list[list[str]] = []
    for filters in groups:
        command = [
            sys.executable,
            "-m",
            "silisocs.studies.run_study",
            "--study",
            study_arg,
            "--repo-root",
            str(repo_root),
            "run",
            # The user already confirmed scale by submitting; submitted jobs
            # run non-interactively so the preflight gate must not block them.
            "--yes",
            "--max-concurrent",
            str(max_concurrent),
        ]
        if timeout_seconds > 0:
            command.extend(["--timeout-seconds", str(timeout_seconds)])
        command.extend(_filter_args_from_mapping(filters))
        commands.append(command)
    return commands


def _wait_for_ready_url(url: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= int(response.status) < 500:
                    return
        except Exception as e:
            last_error = e
        time.sleep(2)
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(f"Server hook did not become ready at {url}{detail}")


def _run_command_with_optional_hooks(
    command: list[str],
    cwd: str,
    setup_command: str | None,
    server_command: str | None,
    server_ready_url: str | None,
    server_timeout_seconds: int,
) -> int:
    """Run one submitted study command with user-owned setup/server hooks."""
    server_proc: subprocess.Popen[str] | None = None
    try:
        if setup_command:
            subprocess.run(setup_command, cwd=cwd, shell=True, check=True, text=True)
        if server_command:
            server_proc = subprocess.Popen(
                server_command,
                cwd=cwd,
                shell=True,
                text=True,
                start_new_session=True,
            )
            if server_ready_url:
                _wait_for_ready_url(server_ready_url, server_timeout_seconds)
        result = subprocess.run(command, cwd=cwd, check=False, text=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, command)
        return int(result.returncode)
    finally:
        if server_proc is not None and server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server_proc.kill()


def _submitit_parameters(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {
        "name": str(args.job_name),
        "timeout_min": int(args.timeout_minutes),
        "cpus_per_task": int(args.cpus_per_task),
    }
    if args.nodes:
        params["nodes"] = int(args.nodes)
    if args.tasks_per_node:
        params["tasks_per_node"] = int(args.tasks_per_node)
    if args.gpus_per_node:
        params["gpus_per_node"] = int(args.gpus_per_node)
    if args.mem_gb:
        params["mem_gb"] = int(args.mem_gb)
    if args.partition:
        params["slurm_partition"] = str(args.partition)
    if args.account:
        params["slurm_account"] = str(args.account)
    if args.constraint:
        params["slurm_constraint"] = str(args.constraint)
    if args.slurm_comment:
        params["slurm_comment"] = str(args.slurm_comment)
    return params


def cmd_submitit(args: argparse.Namespace) -> int:
    """Submit study run groups with Submitit while preserving study-runner semantics."""
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
        args.only_run_id,
    )
    if not run_specs:
        print("No runs matched filters; nothing to submit.")
        return 0

    array_mode = str(args.array_mode).strip().lower()
    groups = _submitit_group_filters(run_specs, array_mode)
    commands = _build_submitit_job_commands(
        study_path=study_path,
        repo_root=repo_root,
        groups=groups,
        max_concurrent=int(args.max_concurrent),
        timeout_seconds=int(args.timeout_seconds),
    )

    print(f"Study: {study['name']}")
    print(f"Matched expanded runs: {len(run_specs)}")
    print(f"Submitit groups ({array_mode}): {len(commands)}")
    for command in commands[: min(len(commands), PLAN_PREVIEW_ROWS)]:
        print(" ".join(shlex.quote(part) for part in command))
    if len(commands) > PLAN_PREVIEW_ROWS:
        print(f"... and {len(commands) - PLAN_PREVIEW_ROWS} more")
    if args.dry_run:
        print("Dry mode: no Submitit jobs were submitted.")
        return 0

    try:
        import submitit
    except ImportError as e:  # pragma: no cover - exercised when optional extra missing
        raise StudyConfigError(
            "Submitit support requires the optional hpc dependencies. "
            "Install with `uv sync --extra hpc` or `pip install silisocs[hpc]`."
        ) from e

    folder = Path(args.folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    executor = submitit.AutoExecutor(folder=str(folder))
    executor.update_parameters(**_submitit_parameters(args))
    jobs = [
        executor.submit(
            _run_command_with_optional_hooks,
            command,
            str(repo_root),
            args.setup_command,
            args.server_command,
            args.server_ready_url,
            int(args.server_timeout_seconds),
        )
        for command in commands
    ]
    print(f"Submitted {len(jobs)} Submitit jobs")
    for job in jobs:
        print(f"- {job.job_id}")
    return 0


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
        getattr(args, "only_run_id", None),
    )

    if not run_specs:
        print("No runs matched filters; nothing to submit.")
        return 0

    array_mode = str(args.array_mode).strip().lower()
    total_tasks = _count_array_tasks(run_specs, array_mode)
    array_spec = f"0-{total_tasks - 1}"

    study_rel = os.path.relpath(study_path, repo_root)
    plan_json = (
        repo_root / "logs" / f"study_plan_{Path(study_rel).stem}_{array_mode}_{now_iso()}.json"
    )
    plan_json.parent.mkdir(parents=True, exist_ok=True)
    _write_json(plan_json, {"schema_version": SCHEMA_VERSION, "plan": _plan_rows(run_specs)})

    export_parts = {
        "REPO_ROOT": str(repo_root),
        "STUDY_FILE": study_rel,
        "PLAN_JSON": str(plan_json),
        "ARRAY_MODE": array_mode,
        "HYPOTHESIS_IDS": _csv_compact(args.only_hypothesis),
        "CONDITION_IDS": _csv_compact(args.only_condition),
        "SUB_EXPERIMENT_IDS": _csv_compact(args.only_sub_experiment),
        "SEED_IDS": _csv_compact(args.only_seed),
        "RUN_IDS": _csv_compact(args.only_run_id),
        "MAX_CONCURRENT": str(int(args.max_concurrent)),
        "RUNNER_PYTHON": str(args.runner_python),
        "SILISOCS_HPC_SETUP_COMMAND": str(args.setup_command or ""),
        "SILISOCS_HPC_SERVER_COMMAND": str(args.server_command or ""),
        "SILISOCS_HPC_SERVER_READY_URL": str(args.server_ready_url or ""),
        "SILISOCS_HPC_SERVER_TIMEOUT_SECONDS": str(int(args.server_timeout_seconds)),
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
    parser = argparse.ArgumentParser(description="Structured study runner for silisocs")
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
    p_plan.add_argument(
        "--only-run-id",
        default=None,
        help="Optional comma-separated expanded run IDs to include",
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
    p_bash.add_argument(
        "--only-run-id",
        default=None,
        help="Optional comma-separated expanded run IDs to include",
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
        "--force",
        action="store_true",
        help="Re-run all runs even when RUN_COMPLETE.json markers exist",
    )
    p_run.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Skip the preflight confirmation when more than "
            f"{PREFLIGHT_CONFIRM_RUN_COUNT} runs would launch"
        ),
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
    p_run.add_argument(
        "--only-run-id",
        default=None,
        help="Optional comma-separated expanded run IDs to include",
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
        "--runner-python",
        default=sys.executable,
        help="Python executable used by the generic array template",
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
        "--only-run-id",
        default=None,
        help="Optional comma-separated expanded run IDs to include",
    )
    p_slurm.add_argument(
        "--submit",
        action="store_true",
        help="Submit to Slurm via sbatch. Otherwise prints command only.",
    )
    p_slurm.add_argument(
        "--setup-command",
        default=None,
        help="Optional user-owned shell command run before each array task",
    )
    p_slurm.add_argument(
        "--server-command",
        default=None,
        help="Optional user-owned long-running server command started before each array task",
    )
    p_slurm.add_argument(
        "--server-ready-url",
        default=None,
        help="Optional URL polled before running each array task",
    )
    p_slurm.add_argument("--server-timeout-seconds", type=int, default=600)
    p_slurm.set_defaults(func=cmd_slurm_array)

    p_submitit = sub.add_parser(
        "submitit",
        help="Submit study run groups with optional hpc dependencies",
    )
    p_submitit.add_argument(
        "--array-mode",
        choices=["case", "seed", "hypothesis", "run"],
        default="case",
        help="Submission granularity: case(default), seed, hypothesis, or run",
    )
    p_submitit.add_argument(
        "--folder",
        default="logs/submitit",
        help="Submitit log/checkpoint folder",
    )
    p_submitit.add_argument(
        "--max-concurrent",
        type=int,
        default=1,
        help="run_study --max-concurrent passed inside each submitted group",
    )
    p_submitit.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Per subprocess timeout passed inside each submitted group",
    )
    p_submitit.add_argument("--job-name", default="silisocs-study")
    p_submitit.add_argument("--timeout-minutes", type=int, default=240)
    p_submitit.add_argument("--cpus-per-task", type=int, default=4)
    p_submitit.add_argument("--nodes", type=int, default=1)
    p_submitit.add_argument("--tasks-per-node", type=int, default=1)
    p_submitit.add_argument("--gpus-per-node", type=int, default=0)
    p_submitit.add_argument("--mem-gb", type=int, default=0)
    p_submitit.add_argument("--partition", default=None)
    p_submitit.add_argument("--account", default=None)
    p_submitit.add_argument("--constraint", default=None)
    p_submitit.add_argument("--slurm-comment", default=None)
    p_submitit.add_argument(
        "--setup-command",
        default=None,
        help="Optional user-owned shell command run before each submitted group",
    )
    p_submitit.add_argument(
        "--server-command",
        default=None,
        help="Optional user-owned long-running server command started for each submitted group",
    )
    p_submitit.add_argument(
        "--server-ready-url",
        default=None,
        help="Optional URL polled before running the submitted group",
    )
    p_submitit.add_argument("--server-timeout-seconds", type=int, default=600)
    p_submitit.add_argument("--only-hypothesis", default=None)
    p_submitit.add_argument("--only-condition", default=None)
    p_submitit.add_argument("--only-sub-experiment", default=None)
    p_submitit.add_argument("--only-seed", default=None)
    p_submitit.add_argument("--only-run-id", default=None)
    p_submitit.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned Submitit commands without submitting jobs",
    )
    p_submitit.set_defaults(func=cmd_submitit)

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
