"""Locate per-run action-event logs, including multi-GM per-GM subdirectories.

Single-GM runs keep a flat ``<run>/action_events.jsonl``. Multi-GM runs isolate
each game master's log under ``<run>/<gm_name>/action_events.jsonl`` so same-type
game masters do not clobber one another (see the checkpoint isolation logic in
``runtime.construction.game_masters``). Evaluation and analysis must therefore
read every per-GM log, not just the flat root file.
"""

from __future__ import annotations

from pathlib import Path

ACTION_EVENTS_FILENAME = "action_events.jsonl"
EXPOSURE_EVENTS_FILENAME = "exposure_events.jsonl"
PROBE_EVENTS_FILENAME = "probe_events.jsonl"
HARNESS_EVENTS_FILENAME = "harness_events.jsonl"


def resolve_event_files(run_dir: str | Path, filename: str) -> list[Path]:
    """Return the per-run event-log file(s) named ``filename`` for a run directory.

    Returns the flat ``<run>/<filename>`` when present (single-GM), else the
    per-GM ``<run>/<gm_name>/<filename>`` files (sorted by name) for multi-GM
    runs. Returns an empty list when no such log exists.
    """
    run = Path(run_dir)
    root = run / filename
    if root.is_file():
        return [root]
    return sorted(p for p in run.glob(f"*/{filename}") if p.is_file())


def resolve_action_event_files(run_dir: str | Path) -> list[Path]:
    """Return the action-event log file(s) for a run directory (see resolve_event_files)."""
    return resolve_event_files(run_dir, ACTION_EVENTS_FILENAME)


def resolve_exposure_event_files(run_dir: str | Path) -> list[Path]:
    """Return the exposure-event log file(s) for a run directory (see resolve_event_files)."""
    return resolve_event_files(run_dir, EXPOSURE_EVENTS_FILENAME)


def resolve_probe_event_files(run_dir: str | Path) -> list[Path]:
    """Return the probe-event log file(s) for a run directory (see resolve_event_files).

    Probe logs are written at the run root (the probe runner is engine-level,
    not per-GM), so this normally returns the flat file; the resolver keeps
    probe discovery uniform with the action/exposure logs.
    """
    return resolve_event_files(run_dir, PROBE_EVENTS_FILENAME)


def resolve_harness_event_files(run_dir: str | Path) -> list[Path]:
    """Return the harness-event log file(s) for a run directory (see resolve_event_files).

    Harness events are written at the backend layer, so they follow the same flat
    (single-GM) vs per-GM ``<gm_name>/`` layout as action/exposure logs.
    """
    return resolve_event_files(run_dir, HARNESS_EVENTS_FILENAME)
