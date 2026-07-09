"""Self-describing run manifest.

Every run writes a single ``run_manifest.json`` at its output root so tools
(dashboards, evaluators, notebooks) can load a run from one file instead of
re-discovering the artifact layout. The manifest points at artifacts rather
than duplicating them — ``effective_config.yaml`` stays the config record and
``sim_metrics.json`` the telemetry record.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from silisocs.evaluations.action_events import (
    resolve_action_event_files,
    resolve_exposure_event_files,
    resolve_probe_event_files,
)
from silisocs.runtime.provenance import environment_provenance

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "run_manifest.json"
MANIFEST_SCHEMA_VERSION = 1

# Degraded-run counters surfaced as run health (mirrors _warn_degraded_health).
_HEALTH_COUNTERS = (
    "agent_turn_failures",
    "action_parse_failures",
    "action_invalid_targets",
    "backend_action_errors",
)


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
        "game_masters": [
            {
                "name": str(getattr(gm, "name", "")),
                "backend_type": getattr(gm, "backend_type", None),
            }
            for gm in (game_masters or [])
        ],
        "llm_usage": meta.get("llm_usage"),
        "health": {name: int(counters.get(name, 0)) for name in _HEALTH_COUNTERS},
        "artifacts": artifacts,
        "provenance": environment_provenance(Path(project_root) if project_root else out),
    }


def write_run_manifest(**kwargs: Any) -> Path | None:
    """Write ``run_manifest.json`` into the run's output directory.

    Never raises: the manifest is a convenience index and must not fail a run
    that already completed (or mask the real error of one that failed).
    """
    output_dir = Path(kwargs["output_dir"])
    try:
        manifest = build_run_manifest(**kwargs)
        path = output_dir / MANIFEST_FILENAME
        path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    except Exception:
        logger.warning("failed to write %s", MANIFEST_FILENAME, exc_info=True)
        return None
    return path
