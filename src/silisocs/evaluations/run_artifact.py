"""Typed run/study artifact loading — the Run Artifact Module.

One interface for loading a finished run: Studio, evaluators, notebooks,
and CLI tools call :func:`load_run` instead of each rediscovering the on-disk
layout. Loading is manifest-first (``run_manifest.json``, written by every run)
with a legacy fallback to file discovery for runs that predate the manifest —
either way the caller sees the same :class:`RunArtifact`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from silisocs.evaluations.action_events import (
    resolve_action_event_files,
    resolve_exposure_event_files,
    resolve_harness_event_files,
    resolve_probe_event_files,
)

_HEALTH_COUNTERS = (
    "agent_turn_failures",
    "action_parse_failures",
    "action_invalid_targets",
    "backend_action_errors",
)


def iter_jsonl(files: list[Path]) -> Iterator[dict[str, Any]]:
    """Stream event rows from JSONL files, skipping blank/malformed lines."""
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class RunArtifact:
    """A loaded run: identity, health, usage, and streaming event access.

    Scalar fields come from the manifest when present, falling back to
    ``sim_metrics.json`` meta for legacy runs; event files come from the
    manifest's artifact index, falling back to the flat/per-GM resolvers.
    """

    def __init__(self, run_dir: Path, manifest: dict[str, Any] | None) -> None:
        self.run_dir = run_dir
        self.manifest = manifest

    @cached_property
    def metrics(self) -> dict[str, Any] | None:
        """The run's ``sim_metrics.json`` payload, or None when absent/malformed."""
        return _load_json(self.run_dir / "sim_metrics.json")

    @property
    def _meta(self) -> dict[str, Any]:
        meta = (self.metrics or {}).get("meta")
        return meta if isinstance(meta, dict) else {}

    def _field(self, key: str) -> Any:
        if self.manifest is not None and self.manifest.get(key) is not None:
            return self.manifest.get(key)
        return self._meta.get(key)

    @property
    def status(self) -> str | None:
        """'success'/'failed' from the manifest; None for legacy runs."""
        return (self.manifest or {}).get("status")

    @property
    def scenario(self) -> str | None:
        return self._field("scenario")

    @property
    def seed(self) -> int | None:
        return self._field("seed")

    @property
    def num_agents(self) -> int | None:
        return self._field("num_agents")

    @property
    def num_steps(self) -> int | None:
        return self._field("num_steps")

    @property
    def llm_name(self) -> str | None:
        return self._field("llm_name")

    @property
    def game_masters(self) -> list[dict[str, Any]]:
        """``[{name, backend_type}]`` layout; empty for legacy runs."""
        layout = (self.manifest or {}).get("game_masters")
        return layout if isinstance(layout, list) else []

    @property
    def llm_usage(self) -> dict[str, Any] | None:
        usage = self._field("llm_usage")
        return usage if isinstance(usage, dict) else None

    @property
    def health(self) -> dict[str, int]:
        """Degraded-run counters (manifest, else sim_metrics counters)."""
        source = (self.manifest or {}).get("health")
        if not isinstance(source, dict):
            source = (self.metrics or {}).get("counters")
        if not isinstance(source, dict):
            return {}
        return {name: int(source.get(name, 0) or 0) for name in _HEALTH_COUNTERS}

    @property
    def provenance(self) -> dict[str, Any]:
        provenance = (self.manifest or {}).get("provenance")
        return provenance if isinstance(provenance, dict) else {}

    # ---- event logs -------------------------------------------------------

    def _event_files(self, manifest_key: str, resolver: Any) -> list[Path]:
        listed = ((self.manifest or {}).get("artifacts") or {}).get(manifest_key)
        if isinstance(listed, list):
            files = [self.run_dir / entry for entry in listed]
            existing = [path for path in files if path.is_file()]
            if existing or not files:
                return existing
        return list(resolver(self.run_dir))

    def action_event_files(self) -> list[Path]:
        return self._event_files("action_events", resolve_action_event_files)

    def exposure_event_files(self) -> list[Path]:
        return self._event_files("exposure_events", resolve_exposure_event_files)

    def probe_event_files(self) -> list[Path]:
        return self._event_files("probe_events", resolve_probe_event_files)

    def harness_event_files(self) -> list[Path]:
        return self._event_files("harness_events", resolve_harness_event_files)

    def iter_actions(self) -> Iterator[dict[str, Any]]:
        return iter_jsonl(self.action_event_files())

    def iter_exposures(self) -> Iterator[dict[str, Any]]:
        return iter_jsonl(self.exposure_event_files())

    def iter_probes(self) -> Iterator[dict[str, Any]]:
        return iter_jsonl(self.probe_event_files())

    def iter_harness_events(self) -> Iterator[dict[str, Any]]:
        return iter_jsonl(self.harness_event_files())


def load_run(run_dir: str | Path) -> RunArtifact:
    """Load a run directory into a :class:`RunArtifact` (manifest-first)."""
    run = Path(run_dir)
    return RunArtifact(run, _load_json(run / "run_manifest.json"))


class StudyArtifact:
    """A loaded study: plan + organized summary (see docs/study_schema.md)."""

    def __init__(self, study_dir: Path) -> None:
        self.study_dir = study_dir

    @cached_property
    def definition(self) -> dict[str, Any] | None:
        """The authored ``study.yaml`` document."""
        for name in ("study.yaml", "study.yml"):
            path = self.study_dir / name
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            except yaml.YAMLError:
                return None
            return data if isinstance(data, dict) else None
        return None

    @cached_property
    def plan(self) -> dict[str, Any] | None:
        return _load_json(self.study_dir / "generated" / "plan.json")

    @cached_property
    def summary(self) -> dict[str, Any] | None:
        path = self.study_dir / "generated" / "organized" / "study_summary.yaml"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        return data if isinstance(data, dict) else None

    @cached_property
    def run_records(self) -> list[dict[str, Any]]:
        """Organized per-run evaluation records across all hypotheses."""
        records: list[dict[str, Any]] = []
        root = self.study_dir / "generated" / "organized"
        for path in sorted(root.glob("*/runs.json")):
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(rows, list):
                continue
            hypothesis = path.parent.name
            records.extend(
                {"hypothesis": hypothesis, **row} for row in rows if isinstance(row, dict)
            )
        return records

    @cached_property
    def progress(self) -> list[dict[str, Any]]:
        """Project the declared condition matrix onto run completion markers."""
        definition = self.definition or {}
        meta = definition.get("study") or {}
        defaults = meta.get("run_defaults") or {}
        scenarios = meta.get("scenarios") or ["default"]
        seed_start = int(defaults.get("seed_start", 1) or 1)
        repeats = int(defaults.get("seed_repeats", 1) or 1)
        rows: list[dict[str, Any]] = []
        for hypothesis_id, hypothesis in (definition.get("hypotheses") or {}).items():
            for condition_id in hypothesis.get("conditions") or {}:
                for scenario in scenarios:
                    for seed in range(seed_start, seed_start + repeats):
                        run_dir = (
                            self.study_dir
                            / "runs"
                            / hypothesis_id
                            / condition_id
                            / scenario
                            / f"seed_{seed}"
                            / "run"
                        )
                        rows.append(
                            {
                                "hypothesis": hypothesis_id,
                                "condition": condition_id,
                                "scenario": scenario,
                                "seed": seed,
                                "status": (
                                    "complete"
                                    if (run_dir / "RUN_COMPLETE.json").is_file()
                                    else "pending"
                                ),
                                "run_dir": str(run_dir),
                            }
                        )
        return rows

    @cached_property
    def repro_lock(self) -> dict[str, Any] | None:
        """The study's ``repro_lock.json`` (environment provenance + run records)."""
        return _load_json(self.study_dir / "generated" / "repro_lock.json")

    @property
    def provenance(self) -> dict[str, Any]:
        provenance = (self.repro_lock or {}).get("environment")
        return provenance if isinstance(provenance, dict) else {}


def load_study(study_dir: str | Path) -> StudyArtifact:
    """Load a study directory (``experiments/studies/<id>``) into a StudyArtifact."""
    return StudyArtifact(Path(study_dir))
