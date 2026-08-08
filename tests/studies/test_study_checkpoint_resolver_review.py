"""Regression tests for the study checkpoint resolver (code-review fix G5).

``studies.execute._find_latest_checkpoint`` previously globbed ``*.json`` and ordered
by ``st_mtime``, which could diverge from the canonical runtime resolver
``runtime.checkpointing.state.resolve_checkpoint_source`` (which globs
``step_*_checkpoint.json`` and orders by parsed step number). When they
disagree, study evaluation reads a different checkpoint than the runtime would
restore. The fix makes ``_find_latest_checkpoint`` defer to the canonical
resolver while preserving its ``None``-on-missing contract.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from silisocs.runtime.checkpointing import resolve_checkpoint_source
from silisocs.studies.execute import _find_latest_checkpoint


def _write_checkpoint(checkpoints_dir: Path, step: int) -> Path:
    path = checkpoints_dir / f"step_{step}_checkpoint.json"
    path.write_text("{}", encoding="utf-8")
    return path


def test_picks_highest_step_ignoring_mtime_and_stray_files(tmp_path: Path) -> None:
    """Highest *step* wins even when an earlier step has a newer mtime, and a
    stray non-``step_*_checkpoint.json`` file in the dir is ignored.
    """
    run_dir = tmp_path / "run"
    checkpoints_dir = run_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True)

    _write_checkpoint(checkpoints_dir, 1)
    early = _write_checkpoint(checkpoints_dir, 2)
    _write_checkpoint(checkpoints_dir, 10)

    # A stray .json that the old mtime/`*.json` glob could have selected.
    stray = checkpoints_dir / "latest.json"
    stray.write_text("{}", encoding="utf-8")

    # Make the earlier-step checkpoint (and the stray) the newest by mtime so an
    # mtime-ordered selection would pick the wrong file.
    future = time.time() + 1000
    os.utime(early, (future, future))
    os.utime(stray, (future + 1, future + 1))

    result = _find_latest_checkpoint(run_dir)
    assert result is not None
    assert result.name == "step_10_checkpoint.json"


def test_matches_resolve_checkpoint_source(tmp_path: Path) -> None:
    """Study selection agrees with the canonical runtime resolver."""
    run_dir = tmp_path / "run"
    checkpoints_dir = run_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True)

    for step in (0, 3, 7, 12, 4):
        _write_checkpoint(checkpoints_dir, step)

    assert _find_latest_checkpoint(run_dir) == resolve_checkpoint_source(run_dir)


def test_returns_none_when_no_checkpoints(tmp_path: Path) -> None:
    """No checkpoints -> None (preserving the prior contract); the canonical
    resolver raises FileNotFoundError in the same situations.
    """
    # Missing run dir entirely.
    missing = tmp_path / "nope"
    assert _find_latest_checkpoint(missing) is None

    # Run dir present but no checkpoints/ subdir.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert _find_latest_checkpoint(run_dir) is None

    # Empty checkpoints/ dir.
    (run_dir / "checkpoints").mkdir()
    assert _find_latest_checkpoint(run_dir) is None

    # checkpoints/ dir with only a stray (non step_*) file.
    (run_dir / "checkpoints" / "summary.json").write_text("{}", encoding="utf-8")
    assert _find_latest_checkpoint(run_dir) is None
