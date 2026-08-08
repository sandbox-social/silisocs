"""Optimistic concurrency for the Studio write paths.

Studio editors save whole documents, so two open tabs (or a tab and an edit
made on disk) used to overwrite each other: the last save won and the other
edit was gone with nothing said. Every write path therefore carries the
fingerprint of the bytes the client based its edit on and refuses a save whose
fingerprint no longer matches what is on disk.

The fingerprint is opt-in at the API level — a request that omits it keeps the
overwrite behaviour scripts and ``curl`` already rely on — and is always sent
by the Studio UI. This is a conflict *check*, not a lock: it reports the
divergence the editor can act on, and does not serialize concurrent writers.
"""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from typing import Any

# The diff exists to answer "what did the other editor change?", not to be a
# full patch: two lines of context, and a hard cap so one reformatted document
# cannot turn a 409 body into a megabyte.
_DIFF_CONTEXT = 2
_DIFF_MAX_LINES = 120


def fingerprint_text(text: str) -> str:
    """Fingerprint a document from its UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def path_fingerprint(path: Path) -> str:
    """Fingerprint a file's raw on-disk bytes; a file that does not exist has none."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _unified_diff(label: str, current: str, other: str, other_label: str) -> str:
    lines = list(
        difflib.unified_diff(
            current.splitlines(),
            other.splitlines(),
            fromfile=f"{label} (on disk)",
            tofile=f"{label} ({other_label})",
            lineterm="",
            n=_DIFF_CONTEXT,
        )
    )
    if len(lines) > _DIFF_MAX_LINES:
        lines = [*lines[:_DIFF_MAX_LINES], f"... {len(lines) - _DIFF_MAX_LINES} more diff lines"]
    return "\n".join(lines)


class SaveConflictError(Exception):
    """A document changed on disk after the client read it."""

    def __init__(
        self,
        label: str,
        path: Path,
        *,
        submitted: str,
        baseline: str | None = None,
    ) -> None:
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        self.label = label
        self.fingerprint = path_fingerprint(path)
        self.detail: dict[str, Any] = {
            "error": "conflict",
            "file": label,
            "fingerprint": self.fingerprint,
            "message": f"{label} changed on disk since you loaded it.",
            # Diffing against the client's baseline shows what the OTHER editor
            # changed, which is what a conflict needs to explain; without one,
            # the submitted text is the only reference available.
            "diff": _unified_diff(
                label,
                current,
                submitted if baseline is None else baseline,
                "your edit" if baseline is None else "your baseline",
            ),
        }
        super().__init__(self.detail["message"])


def check_fingerprint(
    label: str,
    path: Path,
    expected: str | None,
    *,
    submitted: str,
    baseline: str | None = None,
) -> None:
    """Refuse a save whose baseline fingerprint no longer matches the file on disk.

    ``expected`` empty/absent means the client opted out (old clients, scripts),
    which keeps the plain overwrite behaviour.
    """
    if not expected:
        return
    if path_fingerprint(path) != expected:
        raise SaveConflictError(label, path, submitted=submitted, baseline=baseline)
