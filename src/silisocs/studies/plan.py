"""Pure planning layer for study definitions.

Loads and validates a ``study.yaml``, expands it into the concrete run matrix
(hypotheses x conditions x scenarios x seeds), assembles the Hydra override set
and run commands for each expanded run, and resolves the study's output layout
(workspace/generated directories, planned run directories, summary paths). It
also groups the expanded matrix for HPC submission.

Every function here is pure: no subprocess, no filesystem writes, no argparse.
Studio, tests, and analysis code can import this module to reason about what a
study *would* run without pulling in the execution stack
(:mod:`silisocs.studies.execute`) or the CLI (:mod:`silisocs.studies.cli`).
"""

from __future__ import annotations

import copy
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from silisocs.evaluations.study_matrix import resolve_scenarios, resolve_seeds
from silisocs.studies.evaluation_presets import BUILTIN_EVAL_PRESETS
from silisocs.studies.study_artifacts import load_study_definition
from silisocs.studies.study_schema import validate_schema
from silisocs.studies.study_types import (
    EvalSpec,
    RunSpec,
    StudyConfigError,
    ensure_mapping,
    ensure_string_list,
    format_command_template,
    format_template_token,
    resolve_command_tokens,
    resolve_study_id,
)

DEFAULT_RUNNER_MODULE = "silisocs.runtime.runner"
PLAN_PREVIEW_ROWS = 10

# The planning surface the CLI, the execution layer, and tests are allowed to
# call. Everything else in this module is an implementation detail of run
# expansion and stays underscore-private.
__all__ = [
    "DEFAULT_RUNNER_MODULE",
    "PLAN_PREVIEW_ROWS",
    "build_run_command",
    "build_submitit_job_commands",
    "count_array_tasks",
    "csv_compact",
    "expand_runs",
    "filter_run_specs",
    "load_yaml",
    "plan_rows",
    "planned_run_dir",
    "preflight_summary",
    "render_bash_script",
    "resolve_summary_paths",
    "study_generated_dir",
    "submitit_group_filters",
]


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a study definition, re-raising load/parse failures as StudyConfigError."""
    try:
        return load_study_definition(path)
    except (FileNotFoundError, ValueError) as e:
        raise StudyConfigError(str(e)) from e


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


def expand_runs(  # noqa: C901, PLR0912, PLR0915
    study_path: Path, study_data: dict[str, Any]
) -> tuple[list[RunSpec], tuple[EvalSpec, ...], dict[str, Any]]:
    """Validate a study document and expand it into the concrete run matrix.

    Returns the expanded ``RunSpec`` list, the study-global evaluator specs, and
    the validated ``study`` mapping.
    """
    validate_schema(study_data)

    study_root = study_path.parent
    study = ensure_mapping("study", study_data["study"])
    hypotheses = ensure_mapping("hypotheses", study_data["hypotheses"])
    run_defaults = ensure_mapping("study.run_defaults", study.get("run_defaults"))
    base_scenarios = resolve_scenarios(study, run_defaults, error=StudyConfigError)

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
    default_working_directory = run_defaults.get("working_directory")
    if default_working_directory is not None and not isinstance(default_working_directory, str):
        raise StudyConfigError("study.run_defaults.working_directory must be a string")

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
            working_directory = cond_node.get("working_directory", default_working_directory)
            if working_directory is not None and not isinstance(working_directory, str):
                raise StudyConfigError(
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.working_directory must be a string"
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
                            working_directory=working_directory,
                            command_override=None,
                            eval_specs=merged_eval_specs,
                            reused_source=source,
                            reused_eval=ref.get("eval"),
                        )
                    )
                continue

            seeds = resolve_seeds(
                run_defaults,
                cond_node,
                where=f"hypotheses.{hyp_id}.conditions.{cond_id}",
                error=StudyConfigError,
            )
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
                        output_dir = format_template_token(output_root_override, template_context)
                    else:
                        output_dir = (
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
                    resolved_working_directory = (
                        format_template_token(working_directory, template_context)
                        if working_directory
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
                            working_directory=resolved_working_directory,
                            output_dir=output_dir,
                            command_override=command_override,
                            eval_specs=merged_eval_specs,
                        )
                    )

    return run_specs, global_eval_specs, study


def build_run_command(spec: RunSpec) -> list[str]:
    """Render the runner argv for one expanded run (or its ``command`` override)."""
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
    if spec.output_dir:
        # ++ adds-or-overrides: scenario worlds REPLACE the base world group, so a
        # scenario that does not re-declare output_dir would reject a bare override.
        cmd.append(f"++output_dir={_normalize_override_value(spec.output_dir)}")
    cmd.append(f"experiment_name={spec.study_name}")

    for key in sorted(spec.overrides):
        if key in {
            "seed",
            "run_name",
            "output_dir",
            "experiment_name",
        }:
            continue
        cmd.append(f"{key}={_normalize_override_value(spec.overrides[key])}")

    return cmd


def plan_rows(run_specs: list[RunSpec]) -> list[dict[str, Any]]:
    """Project the expanded runs into the JSON-serialisable plan rows."""
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
            "planned_output_dir": spec.output_dir,
            "working_directory": spec.working_directory,
            "mode": spec.execution_mode,
            "reused_source": spec.reused_source,
            "re_evaluate": spec.re_evaluate,
            "overrides": spec.overrides,
            "command": build_run_command(spec) if spec.execution_mode == "run" else None,
            "evaluators": [e.eval_id for e in spec.eval_specs],
        }
        for spec in run_specs
    ]


def render_bash_script(run_specs: list[RunSpec], repo_root: Path) -> str:
    """Render every runnable spec as a portable, self-contained bash script."""
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
        cmd = build_run_command(spec)
        rendered = " ".join(shlex.quote(token) for token in cmd)
        if spec.working_directory:
            working_directory = Path(spec.working_directory).expanduser()
            if not working_directory.is_absolute():
                working_directory = repo_root / working_directory
            rendered = f"(cd {shlex.quote(str(working_directory.resolve()))} && {rendered})"
        lines.append(rendered)
    lines.append("")
    return "\n".join(lines)


def filter_run_specs(
    run_specs: list[RunSpec],
    only_hypothesis: str | None,
    only_condition: str | None,
    only_sub_experiment: str | None,
    only_seed: str | None,
    only_run_id: str | None = None,
) -> list[RunSpec]:
    """Narrow the expanded runs by the CLI's comma-separated ``--only-*`` filters."""
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


def _spec_scale(spec: RunSpec) -> tuple[int | None, int | None]:
    """Extract (num_agents, num_steps) from resolved overrides when derivable."""

    def _as_int(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    return _as_int(spec.overrides.get("num_agents")), _as_int(spec.overrides.get("num_steps"))


def preflight_summary(run_specs: list[RunSpec]) -> list[str]:
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


def planned_run_dir(spec: RunSpec, repo_root: Path) -> Path | None:
    """Resolve the planned output directory the same way _run_one_spec does."""
    if not spec.output_dir:
        return None
    planned = Path(spec.output_dir)
    if not planned.is_absolute():
        planned = (repo_root / planned).resolve()
    return planned


def _study_workspace_dir(repo_root: Path, study: dict[str, Any]) -> Path:
    study_id = resolve_study_id(study)
    return repo_root / "experiments" / "studies" / study_id


def study_generated_dir(repo_root: Path, study: dict[str, Any]) -> Path:
    """Return the study's ``generated/`` directory under the repo workspace."""
    return _study_workspace_dir(repo_root, study) / "generated"


def resolve_summary_paths(repo_root: Path, study_data: dict[str, Any]) -> tuple[Path, Path]:
    """Return the study's (SUMMARY.md, summary_log.jsonl) absolute paths."""
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


def csv_compact(value: str | None) -> str:
    """Normalise a comma-separated filter value, dropping empty entries."""
    if not value:
        return ""
    return ",".join(part.strip() for part in value.split(",") if part.strip())


def count_array_tasks(run_specs: list[RunSpec], array_mode: str) -> int:
    """Count the HPC array tasks the expanded runs produce under ``array_mode``."""
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


def submitit_group_filters(run_specs: list[RunSpec], array_mode: str) -> list[dict[str, str]]:
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


def build_submitit_job_commands(
    *,
    study_path: Path,
    repo_root: Path,
    groups: list[dict[str, str]],
    max_concurrent: int,
    timeout_seconds: int,
) -> list[list[str]]:
    """Build one ``silisocs-study run`` command per submitted group."""
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
