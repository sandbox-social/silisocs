"""Pure launch-request validation and command preparation for Studio."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from silisocs.studio.forms import ScenarioRepository


class ScenarioNotFoundError(ValueError):
    """Raised when a launch names a scenario absent from the repository."""


@dataclass(frozen=True)
class LaunchSpec:
    """Validated process inputs ready for the generic job queue."""

    scenario: str
    command: list[str]
    output_dir: Path
    snapshot: dict[str, Any]
    control_path: Path | None = None


def _config_directory(
    payload: Mapping[str, Any], repository: Path, draft_root: Path
) -> tuple[str, Path, Any]:
    scenario = str(payload.get("scenario") or "").strip()
    config_payload = payload.get("config_yaml")
    if bool(scenario) == (config_payload is not None):
        raise ValueError("Provide exactly one of scenario or config_yaml")
    if config_payload is None:
        scenario = ScenarioRepository.validate_name(scenario)
        config_dir = repository / "scenarios" / scenario / "conf"
        if not config_dir.is_dir():
            raise ScenarioNotFoundError(f"Scenario {scenario!r} not found")
        return scenario, config_dir, None

    if isinstance(config_payload, str):
        parsed = yaml.safe_load(config_payload) or {}
        files = parsed.get("files") if isinstance(parsed, dict) else None
        files = files if files is not None else {"world/default.yaml": config_payload}
    elif isinstance(config_payload, dict):
        files = config_payload.get("files", config_payload)
    else:
        raise ValueError("config_yaml must be YAML text or a file mapping")
    if not isinstance(files, dict) or not all(
        isinstance(name, str) and isinstance(text, str) for name, text in files.items()
    ):
        raise ValueError("config_yaml files must map names to YAML text")

    scenario = ScenarioRepository.validate_name(str(payload.get("name") or "custom_config"))
    drafts = ScenarioRepository(draft_root)
    draft_name = f"launch_{uuid.uuid4().hex[:12]}"
    drafts.save(draft_name, files)
    return scenario, drafts.root / draft_name / "conf", config_payload


def _output_directory(
    payload: Mapping[str, Any], repository: Path, output_root: Path, scenario: str
) -> tuple[Path, list[str]]:
    overrides = payload.get("overrides") or []
    if not isinstance(overrides, list) or not all(isinstance(item, str) for item in overrides):
        raise ValueError("overrides must be a list of strings")
    output_overrides = [
        item.split("=", 1)[1]
        for item in overrides
        if item.lstrip("+").startswith("output_rootname=") and "=" in item
    ]
    if len(output_overrides) > 1 or any(not value.strip() for value in output_overrides):
        raise ValueError("Provide one non-empty output_rootname")

    explicit_output = output_overrides[0] if output_overrides else None
    requested_output = payload.get("output_dir")
    if requested_output is not None and not isinstance(requested_output, str):
        raise ValueError("output_dir must be a path string")
    if explicit_output and requested_output:
        raise ValueError("Use output_dir or an output_rootname override, not both")

    selected = explicit_output or requested_output
    output_dir = (
        Path(selected).expanduser()
        if selected
        else output_root / scenario / f"studio_{uuid.uuid4().hex[:12]}"
    )
    if not output_dir.is_absolute():
        output_dir = repository / output_dir
    if not explicit_output:
        overrides = [*overrides, f"++output_rootname={output_dir.resolve()}"]
    return output_dir.resolve(), overrides


def prepare_launch(
    payload: Mapping[str, Any],
    *,
    repository_root: str | Path,
    output_root: str | Path,
    draft_root: str | Path,
) -> LaunchSpec:
    """Validate a launch payload without importing FastAPI or simulation internals."""
    repository = Path(repository_root).resolve()
    scenario, config_dir, config_payload = _config_directory(
        payload, repository, Path(draft_root).resolve()
    )
    output_dir, overrides = _output_directory(
        payload, repository, Path(output_root).resolve(), scenario
    )
    control_path, overrides = _apply_interactive(payload, output_dir, overrides)
    command = [
        sys.executable,
        "-m",
        "silisocs.runtime.runner",
        "--config-path",
        str(config_dir),
        *overrides,
    ]
    return LaunchSpec(
        scenario=scenario,
        command=command,
        output_dir=output_dir,
        control_path=control_path,
        snapshot={
            "scenario": scenario,
            "config_yaml": config_payload,
            "overrides": overrides,
            "command": command,
        },
    )


def _apply_interactive(
    payload: Mapping[str, Any], output_dir: Path, overrides: list[str]
) -> tuple[Path | None, list[str]]:
    """Return the run-control overrides for an interactive launch.

    Yields the control-file path both Studio (writer) and the runner (reader)
    agree on plus the extended override list, or ``(None, overrides)`` for a
    normal fire-and-forget launch.
    """
    if not payload.get("interactive"):
        return None, overrides
    start_paused = payload.get("start_paused", True)
    if not isinstance(start_paused, bool):
        raise ValueError("start_paused must be a boolean")
    control_path = (output_dir / "run.control").resolve()
    control_overrides = [
        "sim.engine.control.built_in=control_file",
        f"sim.engine.control.control_file={control_path}",
        f"sim.engine.control.start_paused={'true' if start_paused else 'false'}",
    ]
    return control_path, [*overrides, *control_overrides]
