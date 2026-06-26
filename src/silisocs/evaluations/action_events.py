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


def resolve_action_event_files(run_dir: str | Path) -> list[Path]:
    """Return the action-event log file(s) for a run directory.

    Returns the flat ``<run>/action_events.jsonl`` when present (single-GM), else
    the per-GM ``<run>/<gm_name>/action_events.jsonl`` files (sorted by name) for
    multi-GM runs. Returns an empty list when no action log exists.
    """
    run = Path(run_dir)
    root = run / ACTION_EVENTS_FILENAME
    if root.is_file():
        return [root]
    return sorted(p for p in run.glob(f"*/{ACTION_EVENTS_FILENAME}") if p.is_file())
