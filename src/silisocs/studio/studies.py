"""Filesystem-backed study definitions and progress projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from silisocs.evaluations.run_artifact import StudyArtifact
from silisocs.studies.study_schema import validate_schema
from silisocs.studio.save_conflicts import check_fingerprint, path_fingerprint
from silisocs.studio.scenario_repository import leading_comment_block


def evaluation_presets() -> tuple[str, ...]:
    """Return study evaluator presets from the runner's canonical registry."""
    from silisocs.studies.evaluation_presets import BUILTIN_EVAL_PRESETS

    return tuple(sorted(BUILTIN_EVAL_PRESETS))


class StudyRepository:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def validate_id(study_id: str) -> str:
        value = str(study_id).strip()
        if not value or any(part in value for part in ("/", "\\", "..")):
            raise ValueError("Study id must be a safe directory name")
        return value

    @staticmethod
    def new_definition(
        study_id: str,
        *,
        scenario: dict[str, Any] | None = None,
        working_directory: str,
    ) -> dict[str, Any]:
        """Return the starter definition for a brand-new study draft."""
        return {
            "schema_version": 1,
            "study": {
                "name": study_id,
                "study_id": study_id,
                "study_version": "v1",
                "question": "",
                "scenarios": [scenario["name"]] if scenario else [],
                "run_defaults": {
                    "config_path": scenario["config_pattern"] if scenario else "",
                    "working_directory": scenario["source_path"] if scenario else working_directory,
                    "runner_module": "silisocs.runtime.runner",
                    "seed_start": 1,
                    "seed_repeats": 1,
                    "overrides": {},
                },
            },
            "evaluations": [],
            "hypotheses": {
                "h1": {
                    "statement": "",
                    "independent_variable": "",
                    "prediction": "",
                    "status": "planning",
                    "conditions": {"baseline": {"overrides": {}}},
                }
            },
        }

    def definition_path(self, study_id: str) -> Path:
        """Return the study's definition file: the one on disk, else the default name.

        Reads and writes resolve the same path, so a ``study.yml`` study is
        edited in place rather than shadowed by a second ``study.yaml``.
        """
        directory = self.root / self.validate_id(study_id)
        legacy = directory / "study.yml"
        default = directory / "study.yaml"
        return legacy if legacy.is_file() and not default.is_file() else default

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        items = []
        for path in sorted(self.root.iterdir()):
            if path.is_dir() and (
                (path / "study.yaml").is_file() or (path / "study.yml").is_file()
            ):
                item = self.load(
                    path.name,
                    include_definition=False,
                    include_board=False,
                )
                definition_path = path / "study.yaml"
                if not definition_path.is_file():
                    definition_path = path / "study.yml"
                definition = yaml.safe_load(definition_path.read_text(encoding="utf-8")) or {}
                item["run_count"] = planned_run_count(definition)
                items.append(item)
        return items

    def count(self) -> int:
        """Count study definitions without parsing them."""
        if not self.root.is_dir():
            return 0
        return sum(
            1
            for path in self.root.iterdir()
            if path.is_dir() and ((path / "study.yaml").is_file() or (path / "study.yml").is_file())
        )

    def load(
        self,
        study_id: str,
        *,
        include_definition: bool = True,
        include_board: bool = True,
    ) -> dict[str, Any]:
        study_id = self.validate_id(study_id)
        directory = self.root / study_id
        path = self.definition_path(study_id)
        if not path.is_file():
            raise KeyError(study_id)
        definition = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        meta = definition.get("study") or {}
        result = {
            "id": study_id,
            "name": meta.get("name") or study_id,
            "question": meta.get("question") or "",
            "path": str(directory),
            "definition_path": str(path),
            "board": study_board(directory) if include_board else [],
        }
        if include_definition:
            result["definition"] = definition
            result["yaml"] = path.read_text(encoding="utf-8")
            result["fingerprint"] = path_fingerprint(path)
        return result

    def save(
        self,
        study_id: str,
        text: str,
        *,
        fingerprint: str | None = None,
        baseline: str | None = None,
    ) -> dict[str, Any]:
        study_id = self.validate_id(study_id)
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("study.yaml must contain a mapping")
        validate_schema(data)
        path = self.definition_path(study_id)
        check_fingerprint(path.name, path, fingerprint, submitted=text, baseline=baseline)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write the author's text verbatim: re-dumping the parsed document would
        # strip every comment and blank-line grouping the author wrote.
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        return self.load(study_id)


def compose_study(text: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Apply dotted form updates to a study document without losing unknown keys.

    A form edit re-serializes the document, which keeps every key but not the
    comments inside it; the leading comment block is carried over, as the
    scenario composer does. A raw-text edit never goes through this path.
    """
    definition = yaml.safe_load(text) or {}
    if not isinstance(definition, dict):
        raise ValueError("study.yaml must contain a mapping")
    for key, value in updates.items():
        parts = str(key).split(".")
        if not parts or not all(parts):
            raise ValueError(f"Invalid study field path {key!r}")
        cursor = definition
        for part in parts[:-1]:
            child = cursor.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"Cannot write {key!r}; {part!r} is not a mapping")
            cursor = child
        cursor[parts[-1]] = value
    return {
        "definition": definition,
        "yaml": leading_comment_block(text)
        + yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
    }


def study_board(directory: Path) -> list[dict[str, Any]]:
    """Return the typed artifact's progress projection."""
    return StudyArtifact(directory).progress


def planned_run_count(definition: dict[str, Any]) -> int:
    """Return condition x scenario x seed cardinality without filesystem checks."""
    meta = definition.get("study") or {}
    scenarios = meta.get("scenarios") or ["default"]
    repeats = int((meta.get("run_defaults") or {}).get("seed_repeats", 1) or 1)
    conditions = sum(
        len(hypothesis.get("conditions") or {})
        for hypothesis in (definition.get("hypotheses") or {}).values()
        if isinstance(hypothesis, dict)
    )
    return conditions * len(scenarios) * repeats
