"""Filesystem-backed object discovery for local Studio."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from silisocs.evaluations.run_artifact import RunArtifact, load_run


@dataclass(frozen=True)
class RunRecord:
    """Indexed run identity and its loaded artifact."""

    id: str
    path: Path
    artifact: RunArtifact
    modified: float


def discover_runs(root: Path) -> list[RunRecord]:
    """Discover manifest-backed and legacy runs below an output root."""
    candidates = {
        path.parent
        for pattern in ("run_manifest.json", "sim_metrics.json", "action_events.jsonl")
        for path in root.glob(f"**/{pattern}")
        if path.is_file()
    }
    # Multi-GM runs keep per-GM event logs in subdirectories of the run dir;
    # a candidate nested inside another candidate is such a shard, not a run.
    run_dirs = [
        path for path in candidates if not any(parent in candidates for parent in path.parents)
    ]
    records = [
        RunRecord(path.relative_to(root).as_posix(), path, load_run(path), path.stat().st_mtime)
        for path in run_dirs
    ]
    return sorted(records, key=lambda record: record.modified, reverse=True)


def find_run(root: Path, run_id: str) -> RunRecord:
    """Resolve a discovered run id without permitting path traversal."""
    resolved = (root / run_id).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_dir():
        raise KeyError(run_id)
    return RunRecord(run_id, resolved, load_run(resolved), resolved.stat().st_mtime)
