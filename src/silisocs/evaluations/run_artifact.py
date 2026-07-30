"""Typed run/study artifact loading — the Run Artifact Module.

One interface for loading a finished run: Studio, evaluators, notebooks,
and CLI tools call :func:`load_run` instead of each rediscovering the on-disk
layout. Loading requires ``run_manifest.json`` and treats its artifact index as
the authoritative description of the run.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from silisocs.evaluations.vocabulary import HEALTH_COUNTERS

_HEALTH_COUNTERS = tuple(HEALTH_COUNTERS)


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
    """A manifest-backed run with streaming access to its indexed event logs."""

    def __init__(self, run_dir: Path, manifest: dict[str, Any]) -> None:
        self.run_dir = run_dir
        self.manifest = manifest

    @cached_property
    def metrics(self) -> dict[str, Any] | None:
        """The run's ``sim_metrics.json`` payload, or None when absent/malformed."""
        return _load_json(self.run_dir / "sim_metrics.json")

    @property
    def status(self) -> str | None:
        """Return the manifest's run status."""
        return self.manifest.get("status")

    @property
    def scenario(self) -> str | None:
        return self.manifest.get("scenario")

    @property
    def seed(self) -> int | None:
        return self.manifest.get("seed")

    @property
    def num_agents(self) -> int | None:
        return self.manifest.get("num_agents")

    @property
    def num_steps(self) -> int | None:
        return self.manifest.get("num_steps")

    @property
    def llm_name(self) -> str | None:
        return self.manifest.get("llm_name")

    @property
    def game_masters(self) -> list[dict[str, Any]]:
        """Return the manifest's ``[{name, backend_type}]`` layout."""
        layout = self.manifest.get("game_masters")
        return layout if isinstance(layout, list) else []

    @property
    def llm_usage(self) -> dict[str, Any] | None:
        usage = self.manifest.get("llm_usage")
        return usage if isinstance(usage, dict) else None

    @property
    def health(self) -> dict[str, int]:
        """Return degraded-run counters from the manifest.

        ``silent_backends`` is a synthetic counter: the number of the run's
        backends that committed no structured action events (manifest health
        carries their names). Surfacing it as a count keeps Studio's zero=green
        health rendering honest for a run whose panels are blank.
        """
        source = self.manifest.get("health")
        if not isinstance(source, dict):
            return {}
        health = {name: int(source.get(name, 0) or 0) for name in _HEALTH_COUNTERS}
        silent = source.get("silent_backends")
        if isinstance(silent, list):
            health["silent_backends"] = len(silent)
        return health

    @property
    def provenance(self) -> dict[str, Any]:
        provenance = self.manifest.get("provenance")
        return provenance if isinstance(provenance, dict) else {}

    # ---- event logs -------------------------------------------------------

    def _event_files(self, manifest_key: str) -> list[Path]:
        if self.status == "running":
            # A provisional manifest indexes a run still writing its logs: its
            # artifact list is a stale snapshot from launch time, so discover
            # the stream files live (same resolvers the final manifest uses).
            from silisocs.evaluations.action_events import LIVE_STREAM_RESOLVERS

            resolver = LIVE_STREAM_RESOLVERS.get(manifest_key)
            return resolver(self.run_dir) if resolver else []
        artifacts = self.manifest.get("artifacts")
        listed = artifacts.get(manifest_key) if isinstance(artifacts, dict) else None
        if not isinstance(listed, list):
            return []
        files = [self.run_dir / entry for entry in listed if isinstance(entry, str)]
        missing = [path for path in files if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                f"Run manifest references missing {manifest_key} artifact: {missing[0]}"
            )
        return files

    def action_event_files(self) -> list[Path]:
        return self._event_files("action_events")

    def exposure_event_files(self) -> list[Path]:
        return self._event_files("exposure_events")

    def probe_event_files(self) -> list[Path]:
        return self._event_files("probe_events")

    def harness_event_files(self) -> list[Path]:
        return self._event_files("harness_events")

    def iter_actions(self) -> Iterator[dict[str, Any]]:
        return iter_jsonl(self.action_event_files())

    def iter_exposures(self) -> Iterator[dict[str, Any]]:
        return iter_jsonl(self.exposure_event_files())

    def iter_probes(self) -> Iterator[dict[str, Any]]:
        return iter_jsonl(self.probe_event_files())

    def iter_harness_events(self) -> Iterator[dict[str, Any]]:
        return iter_jsonl(self.harness_event_files())

    # Materialized views of the streams above, parsed once per artifact.
    #
    # The iter_* methods re-read from disk on every call, which is right for a
    # one-pass consumer over a large log (evaluators, the report CLI). A page
    # that builds several panels is the opposite case: each panel would re-parse
    # the same file, so they share one parse through these instead. Holding a
    # run's events costs no more than the panels that already list() them.

    @cached_property
    def actions(self) -> list[dict[str, Any]]:
        """Every action event, parsed once for this artifact instance."""
        return list(self.iter_actions())

    @cached_property
    def exposures(self) -> list[dict[str, Any]]:
        """Every exposure event, parsed once for this artifact instance."""
        return list(self.iter_exposures())

    @cached_property
    def probes(self) -> list[dict[str, Any]]:
        """Every probe event, parsed once for this artifact instance."""
        return list(self.iter_probes())


def load_run(run_dir: str | Path) -> RunArtifact:
    """Load a run from its required ``run_manifest.json``."""
    run = Path(run_dir)
    manifest_path = run / "run_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Run manifest not found: {manifest_path}")
    manifest = _load_json(manifest_path)
    if manifest is None:
        raise ValueError(f"Run manifest must be a valid JSON object: {manifest_path}")
    return RunArtifact(run, manifest)


def _string_list(value: Any) -> list[str]:
    """Tolerant twin of the runner's ``ensure_string_list`` for progress projection."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int)) and str(item)]
    return []


def _resolve_study_seeds(run_defaults: dict[str, Any], node: dict[str, Any]) -> list[int] | None:
    """Mirror ``run_study._resolve_seeds`` tolerantly (None when unresolvable).

    Honors condition ``seeds``/``seed``/``seed_repeats``/``seed_start`` and the
    run-defaults ``seeds``/``seed``/``seed_repeats``/``seed_start`` fallbacks, so
    an explicit-seed study is projected the same way the runner enumerates it.
    """

    def _int_list(values: Any) -> list[int] | None:
        if isinstance(values, list) and all(isinstance(v, int) for v in values):
            return list(values)
        return None

    if "seeds" in node:
        return _int_list(node["seeds"])
    if "seed" in node:
        return [node["seed"]] if isinstance(node["seed"], int) else None

    repeats = node.get("seed_repeats", run_defaults.get("seed_repeats"))
    if repeats is not None:
        if not isinstance(repeats, int) or repeats <= 0:
            return None
        seed_start = node.get(
            "seed_start", run_defaults.get("seed_start", run_defaults.get("seed", 1))
        )
        if not isinstance(seed_start, int):
            return None
        return [seed_start + i for i in range(repeats)]

    if "seeds" in run_defaults:
        return _int_list(run_defaults["seeds"])

    seed = run_defaults.get("seed", 1)
    return [seed] if isinstance(seed, int) else None


def _resolve_study_scenarios(meta: dict[str, Any], run_defaults: dict[str, Any]) -> list[str]:
    """Mirror ``run_study._resolve_scenarios`` (study.scenarios / base_scenarios / run default)."""
    scenarios = _string_list(meta.get("scenarios"))
    if not scenarios:
        scenarios = _string_list(meta.get("base_scenarios"))
    if not scenarios:
        scenario = run_defaults.get("scenario")
        if isinstance(scenario, str) and scenario:
            scenarios = [scenario]
    return scenarios or ["default"]


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
        """Project the declared condition matrix onto run completion markers.

        Enumerates the (hypothesis, condition, scenario, seed) matrix the *runner*
        actually produces: seeds from condition/run-default ``seeds``/``seed``/
        ``seed_repeats`` (not only ``seed_start``+``seed_repeats``), scenarios from
        condition ``scenarios`` else study ``scenarios``/``base_scenarios``/
        run-default ``scenario``. Cases whose on-disk path this projection cannot
        model — ``reuse_existing`` conditions and ``output_root_override`` custom
        run-dir templates — are marked (``reused``/``skipped``) rather than
        reported as false ``pending`` runs.
        """
        definition = self.definition or {}
        meta = definition.get("study") or {}
        run_defaults = meta.get("run_defaults") or {}
        run_defaults = run_defaults if isinstance(run_defaults, dict) else {}
        base_scenarios = _resolve_study_scenarios(meta, run_defaults)
        default_output_override = run_defaults.get("output_root_override")

        rows: list[dict[str, Any]] = []
        for hypothesis_id, hypothesis in (definition.get("hypotheses") or {}).items():
            if not isinstance(hypothesis, dict):
                continue
            conditions = hypothesis.get("conditions") or {}
            if not isinstance(conditions, dict):
                continue
            for condition_id, condition in conditions.items():
                condition = condition if isinstance(condition, dict) else {}
                execution = condition.get("execution")
                execution = execution if isinstance(execution, dict) else {}
                mode = str(execution.get("mode", "run"))
                output_override = condition.get(
                    "output_root_override",
                    execution.get("output_root_override", default_output_override),
                )

                if mode == "reuse_existing":
                    rows.extend(self._reuse_progress_rows(hypothesis_id, condition_id, condition))
                    continue

                cond_scenarios = _string_list(condition.get("scenarios")) or base_scenarios
                seeds = _resolve_study_seeds(run_defaults, condition)
                if seeds is None:
                    continue

                for scenario in cond_scenarios:
                    for seed in seeds:
                        rows.append(
                            self._matrix_progress_row(
                                hypothesis_id, condition_id, scenario, seed, bool(output_override)
                            )
                        )
        return rows

    def _matrix_progress_row(
        self,
        hypothesis_id: str,
        condition_id: str,
        scenario: str,
        seed: int,
        has_output_override: bool,
    ) -> dict[str, Any]:
        """One (hyp, cond, scenario, seed) row for the default run-dir layout.

        A custom ``output_root_override`` puts the run at a path this projection
        does not model, so its completion is reported ``skipped`` (unknown), never
        falsely ``pending``.
        """
        run_dir = (
            self.study_dir
            / "runs"
            / hypothesis_id
            / condition_id
            / scenario
            / f"seed_{seed}"
            / "run"
        )
        if has_output_override:
            status = "skipped"
        else:
            status = "complete" if (run_dir / "RUN_COMPLETE.json").is_file() else "pending"
        return {
            "hypothesis": hypothesis_id,
            "condition": condition_id,
            "scenario": scenario,
            "seed": seed,
            "status": status,
            "run_dir": str(run_dir),
        }

    def _reuse_progress_rows(
        self, hypothesis_id: str, condition_id: str, condition: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Rows for a ``reuse_existing`` condition (runs live at external sources)."""
        reuse = condition.get("reuse")
        reuse = reuse if isinstance(reuse, dict) else {}
        runs = reuse.get("runs")
        runs = runs if isinstance(runs, list) else []
        rows: list[dict[str, Any]] = []
        for ref in runs:
            if not isinstance(ref, dict):
                continue
            rows.append(
                {
                    "hypothesis": hypothesis_id,
                    "condition": condition_id,
                    "scenario": ref.get("scenario"),
                    "seed": ref.get("seed"),
                    "status": "reused",
                    "run_dir": str(ref.get("source") or ""),
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
