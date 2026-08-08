"""Purpose-built run event log: the runner's live signal to observers.

The runner appends one JSON row per lifecycle event to ``run_events.jsonl``
at the run root — step boundaries from the loop strategy, status transitions
from the session. Observers (Studio's SSE loop, scripts) TAIL this one file
instead of inferring liveness from artifact side effects (checkpoint
filenames, event-log globs, PID probes).

Rows are versioned (``{"v": 1, "ts": ..., "kind": ..., ...}``) and
append-only; a resumed run keeps appending to the same file, so consumers
track their own byte offsets. Emission is best-effort: observability must
never fail a run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

RUN_EVENTS_FILENAME = "run_events.jsonl"
RUN_EVENTS_SCHEMA_VERSION = 1


class RunEventLog:
    """Append-only JSONL feed of run lifecycle events (never raises)."""

    def __init__(self, output_dir: str | Path) -> None:
        self._path = Path(output_dir) / RUN_EVENTS_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, kind: str, **fields: Any) -> None:
        row = {"v": RUN_EVENTS_SCHEMA_VERSION, "ts": round(time.time(), 3), "kind": kind, **fields}
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
        except OSError:
            pass
