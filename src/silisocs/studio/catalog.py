"""Filesystem-backed object discovery for local Studio."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from silisocs.evaluations.action_events import resolve_action_event_files
from silisocs.evaluations.run_artifact import RunArtifact, load_run

# A run is defined by its manifest: the run root is the one directory that holds
# ``run_manifest.json`` (per-GM event-log subdirectories never do), and it is the
# only shape ``load_run`` can read.
_RUN_MARKERS = frozenset({"run_manifest.json"})

# Discovery walks the whole output tree, which is slow when outputs live on a
# network filesystem, and every list page plus the command palette asks for it.
# Runs do not appear faster than a person clicks, so a brief memo removes the
# repeat walks without anyone noticing staleness.
_DISCOVERY_TTL_SECONDS = 3.0
_discovery_cache: dict[tuple[Path, Path], tuple[float, list[RunRecord]]] = {}

# ``RunArtifact.actions`` (and the sibling event views) is a per-instance
# cached_property that parses the whole event log on first touch. Every panel and
# view request otherwise builds a fresh artifact and re-parses from scratch. Keyed
# by resolved run path, a run's artifact is reused until its action-event logs
# change size/mtime — so an unchanged run parses once and a live run growing on
# disk invalidates itself. A dict + lock keeps concurrent HTTP workers safe.
_ArtifactSignature = tuple[tuple[str, float, int], ...]
_artifact_cache: dict[Path, tuple[_ArtifactSignature, RunRecord]] = {}
_artifact_cache_lock = threading.Lock()


def _action_events_signature(resolved: Path) -> _ArtifactSignature:
    """Fingerprint a run so on-disk changes invalidate the cached artifact.

    Covers the action-event logs (a live run growing) AND the run manifest —
    the final manifest REPLACES the provisional one at completion, usually
    after the last event row landed, so without it a finished run would keep
    serving its cached ``running`` record forever.
    """
    signature: list[tuple[str, float, int]] = []
    paths = resolve_action_event_files(resolved)
    paths.append(resolved / "run_manifest.json")
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((path.as_posix(), stat.st_mtime, stat.st_size))
    return tuple(signature)


@dataclass(frozen=True)
class RunRecord:
    """Indexed run identity and its loaded artifact."""

    id: str
    path: Path
    artifact: RunArtifact
    modified: float


def _run_directories(root: Path) -> list[Path]:
    """Every run directory below an output root, in one walk."""
    candidates: set[Path] = set()
    for dirpath, _, filenames in os.walk(root):
        if _RUN_MARKERS.intersection(filenames):
            candidates.add(Path(dirpath))
    # Multi-GM runs keep per-GM event logs in subdirectories of the run dir;
    # a candidate nested inside another candidate is such a shard, not a run.
    return [path for path in candidates if not any(parent in candidates for parent in path.parents)]


def discover_runs(
    root: Path,
    *,
    use_cache: bool = True,
    subtree: str | Path | None = None,
) -> list[RunRecord]:
    """Discover manifest-backed runs, optionally below one safe output subtree.

    ``subtree`` keeps contextual lookups proportional to their subject. A
    scenario page, for example, should not walk every other scenario's
    artifacts merely to populate its own history.
    """
    root = root.resolve()
    scan_root = (root / subtree).resolve() if subtree is not None else root
    if not scan_root.is_relative_to(root):
        raise ValueError("Run discovery subtree must stay inside the output root")
    if not scan_root.is_dir():
        return []

    now = time.monotonic()
    cache_key = (root, scan_root)
    cached = _discovery_cache.get(cache_key) if use_cache else None
    if cached is not None and now - cached[0] < _DISCOVERY_TTL_SECONDS:
        return cached[1]
    records = [
        RunRecord(path.relative_to(root).as_posix(), path, load_run(path), path.stat().st_mtime)
        for path in _run_directories(scan_root)
    ]
    records.sort(key=lambda record: record.modified, reverse=True)
    _discovery_cache[cache_key] = (now, records)
    return records


def arrange_runs(
    records: list[RunRecord],
    *,
    query: str = "",
    status: str = "",
    sort: str = "recent",
) -> list[RunRecord]:
    """Filter and order discovered runs for the archive (pure, presentation-only)."""
    needle = query.strip().lower()

    def keep(record: RunRecord) -> bool:
        artifact = record.artifact
        if status and (artifact.status or "") != status:
            return False
        if needle:
            haystack = f"{record.id} {artifact.scenario or ''} {artifact.status or ''}".lower()
            return needle in haystack
        return True

    orders = {
        "recent": lambda record: (-record.modified,),
        "scenario": lambda record: ((record.artifact.scenario or "").lower(), -record.modified),
        "status": lambda record: (record.artifact.status or "", -record.modified),
        "episodes": lambda record: (-(record.artifact.num_steps or 0), -record.modified),
    }
    return sorted(
        (record for record in records if keep(record)), key=orders.get(sort, orders["recent"])
    )


def clear_discovery_cache() -> None:
    """Forget memoized discovery (tests, and anything that just wrote a run)."""
    _discovery_cache.clear()
    with _artifact_cache_lock:
        _artifact_cache.clear()


def find_run(root: Path, run_id: str, *, use_cache: bool = True) -> RunRecord:
    """Resolve a discovered run id without permitting path traversal.

    Repeated requests for the same unchanged run reuse one ``RunArtifact`` (and
    thus its parsed-once event views) via ``_artifact_cache``.
    """
    resolved = (root / run_id).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_dir():
        raise KeyError(run_id)
    signature = _action_events_signature(resolved)
    if use_cache:
        with _artifact_cache_lock:
            cached = _artifact_cache.get(resolved)
        if cached is not None and cached[0] == signature:
            return cached[1]
    record = RunRecord(run_id, resolved, load_run(resolved), resolved.stat().st_mtime)
    with _artifact_cache_lock:
        _artifact_cache[resolved] = (signature, record)
    return record
