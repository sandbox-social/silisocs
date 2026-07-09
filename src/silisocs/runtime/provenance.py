"""Execution-environment provenance shared by runs and studies.

Config snapshots alone cannot reproduce a run: results depend on the code
revision and dependency set that executed it. This module captures that
environment once; the study runner stamps it into ``plan.json`` and every run
stamps it into ``run_manifest.json``.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def hash_file(path: Path) -> str | None:
    """Return the sha256 hex digest of ``path``, or None when it is not a file."""
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def environment_provenance(repo_root: Path) -> dict[str, Any]:
    """Capture the execution environment so a run or study can be reproduced.

    Every field degrades to ``None`` outside a git checkout / installed package,
    so this is safe to call from a pip-installed engine.
    """

    def _git(*git_args: str) -> str | None:
        try:
            proc = subprocess.run(
                ["git", *git_args],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    try:
        silisocs_version = importlib.metadata.version("silisocs")
    except importlib.metadata.PackageNotFoundError:
        silisocs_version = None

    dirty_output = _git("status", "--porcelain")
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(dirty_output) if dirty_output is not None else None,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "silisocs_version": silisocs_version,
        "uv_lock_sha256": hash_file(repo_root / "uv.lock"),
        "captured_at": datetime.now(UTC).isoformat(),
    }
