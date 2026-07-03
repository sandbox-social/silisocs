"""Shared types and leaf helpers for study definitions.

Pure, dependency-light building blocks reused across the study pipeline (schema
validation, run expansion, execution, and the CLI): the study exception, the
expanded run/eval spec dataclasses, and the small validators/formatters they all
share. Kept in a leaf module (no other ``studies`` imports) so the heavier stages
can depend on it without import cycles.
"""

from __future__ import annotations

import datetime as dt
import shlex
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1


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
