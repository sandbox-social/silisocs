"""I/O utilities for study definitions.

Handles loading, validating, and extracting metadata from study.yaml files and
their associated Hydra output directories.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent


def load_study_definition(path: Path) -> dict[str, Any]:
    """Parse and return the study definition YAML."""
    with path.open() as f:
        data = cast("dict[str, Any]", yaml.safe_load(f))
    for key in ("study", "hypotheses"):
        if key not in data:
            print(f"Error: study definition missing required key '{key}'", file=sys.stderr)
            sys.exit(1)
    return data


def validate_sources(data: dict[str, Any]) -> list[str]:
    """Check that all referenced source and eval paths exist. Return list of errors."""
    errors: list[str] = []
    for hyp_id, hyp in data["hypotheses"].items():
        for cond_name, cond in hyp["conditions"].items():
            for run in cond["runs"]:
                source = PROJECT_ROOT / run["source"]
                if not source.is_dir():
                    errors.append(f"[{hyp_id}/{cond_name}] source dir missing: {source}")
                eval_path = PROJECT_ROOT / run["eval"]
                if not eval_path.is_file():
                    errors.append(f"[{hyp_id}/{cond_name}] eval file missing: {eval_path}")
    return errors


def extract_run_metadata(source_dir: Path, base_config: str | None = None) -> dict[str, Any]:
    """Extract key metadata from a Hydra output directory.

    Reads ``.hydra/config.yaml`` for resolved config values and
    ``.hydra/overrides.yaml`` for the original CLI overrides, which are used
    to construct a ``run_command`` that reproduces the run.
    """
    hydra_config = source_dir / ".hydra" / "config.yaml"
    metadata: dict[str, Any] = {"source": str(source_dir.relative_to(PROJECT_ROOT))}
    if not hydra_config.is_file():
        return metadata
    with hydra_config.open() as f:
        cfg = yaml.safe_load(f)

    model = cfg.get("model", {})
    metadata["model_name"] = model.get("model_name", model.get("name"))
    metadata["model_config"] = model.get("name")

    scenario = cfg.get("scenario", {})
    metadata["scenario"] = scenario.get("name")
    metadata["scenario_description"] = scenario.get("description")

    execution = cfg.get("simulation", {}).get("execution", {})
    metadata["max_steps"] = execution.get("max_steps")

    metadata["seed"] = cfg.get("experiment", {}).get("seed")

    overrides_path = source_dir / ".hydra" / "overrides.yaml"
    if overrides_path.is_file():
        with overrides_path.open() as f:
            cli_overrides: list[str] = yaml.safe_load(f) or []
        metadata["cli_overrides"] = cli_overrides
        entry_point = "uv run python run_experiment.py"
        if base_config:
            entry_point += f"  # defaults from {base_config}"
        metadata["run_command"] = entry_point + " " + " ".join(cli_overrides)

    return metadata
