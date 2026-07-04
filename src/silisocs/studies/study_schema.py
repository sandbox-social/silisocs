"""Schema validation for study definition files (schema_version 1)."""

from __future__ import annotations

from typing import Any

from silisocs.studies.study_types import (
    SCHEMA_VERSION,
    StudyConfigError,
    ensure_mapping,
    ensure_string_list,
    resolve_command_tokens,
    resolve_study_id,
)


def validate_schema(study_data: dict[str, Any]) -> None:  # noqa: C901, PLR0912, PLR0915
    """Validate ``study_data`` against study schema version 1."""
    schema_version = study_data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise StudyConfigError(
            f"Unsupported schema_version={schema_version}; expected {SCHEMA_VERSION}"
        )
    if "evaluation" in study_data:
        raise StudyConfigError("Use top-level evaluations: [...] instead of evaluation")

    study = ensure_mapping("study", study_data.get("study"))
    if not isinstance(study.get("name"), str) or not study["name"].strip():
        raise StudyConfigError("study.name is required and must be a non-empty string")
    resolve_study_id(study)

    if "parent_studies" in study:
        ensure_string_list("study.parent_studies", study.get("parent_studies"))

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

    run_defaults = ensure_mapping("study.run_defaults", study.get("run_defaults"))
    if "overrides" in run_defaults and not isinstance(run_defaults["overrides"], dict):
        raise StudyConfigError("study.run_defaults.overrides must be a mapping")

    hypotheses = ensure_mapping("hypotheses", study_data.get("hypotheses"))
    if not hypotheses:
        raise StudyConfigError("hypotheses must include at least one hypothesis")

    for hyp_id, hyp_node in hypotheses.items():
        if not isinstance(hyp_node, dict):
            raise StudyConfigError(f"hypotheses.{hyp_id} must be a mapping")
        if "conditions" not in hyp_node:
            raise StudyConfigError(f"hypotheses.{hyp_id} must define conditions")

        cond_map = hyp_node.get("conditions")
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
            execution = ensure_mapping(
                f"hypotheses.{hyp_id}.conditions.{cond_id}.execution",
                cond_node.get("execution"),
            )
            mode = str(execution.get("mode", "run"))
            if mode not in {"run", "reuse_existing"}:
                raise StudyConfigError(
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.execution.mode must be run or reuse_existing"
                )
            if "command" in execution:
                resolve_command_tokens(
                    execution.get("command"),
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.execution.command",
                )
            if mode == "reuse_existing":
                reuse = ensure_mapping(
                    f"hypotheses.{hyp_id}.conditions.{cond_id}.reuse",
                    cond_node.get("reuse"),
                )
                runs = reuse.get("runs")
                if not isinstance(runs, list) or not runs:
                    raise StudyConfigError(
                        f"hypotheses.{hyp_id}.conditions.{cond_id}.reuse.runs must be a non-empty list"
                    )
