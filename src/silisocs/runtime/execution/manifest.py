"""Self-describing run manifest.

Every run writes a single ``run_manifest.json`` at its output root so tools
(Studio, evaluators, notebooks) can load a run from one file instead of
re-discovering the artifact layout. The manifest points at artifacts rather
than duplicating them — ``effective_config.yaml`` stays the config record and
``sim_metrics.json`` the telemetry record.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Collection, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from silisocs.evaluations.action_events import (
    resolve_action_event_files,
    resolve_exposure_event_files,
    resolve_harness_event_files,
    resolve_probe_event_files,
)
from silisocs.evaluations.vocabulary import (
    HEALTH_COUNTERS,
    EventSemantics,
    derive_event_semantics,
    event_semantics_for,
    parse_event_semantics,
)
from silisocs.runtime.provenance import environment_provenance

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "run_manifest.json"
MANIFEST_SCHEMA_VERSION = 1

# Degraded-run counters surfaced as run health: iterate the live registry
# (evaluations.vocabulary.register_health_counter) at use time, never snapshot.


def _portable_event_semantics(value: Any) -> dict[str, Any] | None:
    """Normalize optional backend visual metadata without risking the manifest."""
    if isinstance(value, EventSemantics):
        value = {
            "roles": value.roles,
            "fields": value.fields,
            "labels": value.label_tags,
        }
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for section, entries in value.items():
        if not isinstance(entries, Mapping):
            continue
        normalized: dict[str, list[str]] = {}
        for name, values in entries.items():
            if not isinstance(values, Collection) or isinstance(values, (str, bytes)):
                continue
            items = [str(item) for item in values]
            normalized[str(name)] = sorted(items) if section == "roles" else items
        if normalized:
            result[str(section)] = normalized
    return result or None


def _game_master_record(gm: Any, output_dir: Path) -> dict[str, Any]:
    backend = getattr(gm, "backend", None)
    visualizer = getattr(type(backend), "visualizer", None) if backend is not None else None
    db_path = getattr(backend, "db_path", None)
    backend_type = str(getattr(gm, "backend_type", "") or "")
    event_semantics = event_semantics_for(backend_type) if backend else None
    if (
        backend is not None
        and event_semantics is not None
        and not (event_semantics.roles or event_semantics.fields or event_semantics.label_tags)
    ):
        event_semantics = parse_event_semantics(getattr(type(backend), "event_semantics", None))
        if not (event_semantics.roles or event_semantics.fields or event_semantics.label_tags):
            event_semantics = derive_event_semantics(type(backend))
    try:
        relative_db = (
            str(Path(db_path).resolve().relative_to(output_dir.resolve())) if db_path else None
        )
    except (OSError, ValueError):
        relative_db = str(db_path) if db_path else None
    return {
        "name": str(getattr(gm, "name", "")),
        "backend_type": backend_type or None,
        "backend_class_path": (
            f"{type(backend).__module__}.{type(backend).__qualname__}"
            if backend is not None
            else None
        ),
        "database": relative_db,
        "visualizer": asdict(visualizer)
        if visualizer is not None and is_dataclass(visualizer)
        else None,
        "event_semantics": _portable_event_semantics(event_semantics),
    }


def backend_committed_nothing(game_master: Any) -> bool:
    """Whether a game master's backend logged no committed action events.

    The committed-events mirror is the authoritative "did anything happen" signal;
    a backend whose actions bypass invoke-layer auto-logging and never call
    ``_log_action_event`` runs to completion and leaves every analysis panel blank.
    Shared by the run-end warning
    (``session._warn_silent_backends``) and the persisted health block below so both
    judge silence identically.
    """
    backend = getattr(game_master, "backend", None)
    counter = getattr(backend, "count_committed_events", None)
    if backend is None or not callable(counter):
        return False
    # No guard: swallowing a failure here would suppress the very NO-ACTION-EVENTS
    # warning this function computes, reporting a silent backend as healthy.
    return not counter()


def _silent_backend_names(game_masters: Collection[Any]) -> list[str]:
    """Names of game masters whose backend committed no structured action events.

    Persisting this into the health block means Studio's Run-health panel surfaces
    a run whose panels are blank because its backend produced no structured
    committed events — otherwise the panel reads all-green.
    """
    return [
        str(getattr(gm, "name", "") or getattr(gm, "backend_type", "") or "?")
        for gm in game_masters or []
        if backend_committed_nothing(gm)
    ]


def _relative_if_present(output_dir: Path, name: str) -> str | None:
    return name if (output_dir / name).is_file() else None


def build_run_manifest(
    *,
    output_dir: str | Path,
    status: str,
    error: str = "",
    meta: dict[str, Any] | None = None,
    counters: dict[str, int] | None = None,
    game_masters: list[Any] | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Assemble the manifest payload for a finished (or failed) run.

    ``meta``/``counters`` are the run's telemetry meta and counter maps (the
    same data written to ``sim_metrics.json``); ``game_masters`` are the live
    GM objects, used only for their ``name``/``backend_type`` layout.
    """
    out = Path(output_dir)
    meta = meta or {}
    counters = counters or {}
    artifacts: dict[str, Any] = {
        key: _relative_if_present(out, filename)
        for key, filename in (
            ("effective_config", "effective_config.yaml"),
            ("sim_metrics", "sim_metrics.json"),
            ("run_stats", "run_stats.log"),
            ("prompts_and_responses", "prompts_and_responses.jsonl"),
        )
    }
    # Event logs may be flat or per-GM (<run>/<gm>/...); record the real files.
    for key, resolver in (
        ("action_events", resolve_action_event_files),
        ("exposure_events", resolve_exposure_event_files),
        ("probe_events", resolve_probe_event_files),
        ("harness_events", resolve_harness_event_files),
    ):
        artifacts[key] = [str(path.relative_to(out)) for path in resolver(out)]
    checkpoint_dir = out / "checkpoints"
    artifacts["checkpoints"] = "checkpoints" if checkpoint_dir.is_dir() else None

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "status": status,
        "error": error or None,
        "scenario": meta.get("scenario"),
        "seed": meta.get("seed"),
        "num_agents": meta.get("num_agents"),
        "num_steps": meta.get("num_steps"),
        "llm_name": meta.get("llm_name"),
        "game_masters": [_game_master_record(gm, out) for gm in (game_masters or [])],
        "llm_usage": meta.get("llm_usage"),
        "health": {
            **{name: int(counters.get(name, 0)) for name in HEALTH_COUNTERS},
            "silent_backends": _silent_backend_names(game_masters or []),
        },
        "artifacts": artifacts,
        "provenance": environment_provenance(Path(project_root) if project_root else out),
    }


def write_run_manifest(**kwargs: Any) -> Path | None:
    """Write ``run_manifest.json`` into the run's output directory.

    Never raises: the manifest is a convenience index and must not fail a run
    that already completed (or mask the real error of one that failed).
    """
    output_dir = Path(kwargs["output_dir"])
    tmp: Path | None = None
    try:
        manifest = build_run_manifest(**kwargs)
        path = output_dir / MANIFEST_FILENAME
        # Temp file + atomic rename: readers (Studio's catalog re-loads the
        # run the moment this file's stat changes) must never see a
        # half-written manifest, especially at the provisional->final rewrite.
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logger.warning("failed to write %s", MANIFEST_FILENAME, exc_info=True)
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
        return None
    return path
