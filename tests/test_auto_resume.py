"""Tests for checkpoint auto-resume helpers (has_checkpoints / select_resume_source)."""

from __future__ import annotations

from pathlib import Path

from silisocs.runtime.checkpointing import has_checkpoints, select_resume_source


def _write_checkpoint(output_dir: Path, step: int = 1) -> None:
    checkpoints = output_dir / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    (checkpoints / f"step_{step}_checkpoint.json").write_text("{}", encoding="utf-8")


def test_has_checkpoints_missing_dir(tmp_path: Path) -> None:
    assert has_checkpoints(tmp_path / "does_not_exist") is False


def test_has_checkpoints_empty_dir(tmp_path: Path) -> None:
    (tmp_path / "checkpoints").mkdir()
    assert has_checkpoints(tmp_path) is False


def test_has_checkpoints_with_checkpoint(tmp_path: Path) -> None:
    _write_checkpoint(tmp_path, step=1)
    assert has_checkpoints(tmp_path) is True


def test_has_checkpoints_ignores_unrelated_files(tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    (checkpoints / "notes.txt").write_text("x", encoding="utf-8")
    assert has_checkpoints(tmp_path) is False


def test_select_explicit_source_run_wins(tmp_path: Path) -> None:
    # Explicit source_run always selected, even without its own checkpoints
    # and regardless of auto_resume.
    source = tmp_path / "prior_run"
    source.mkdir()
    output = tmp_path / "this_run"
    output.mkdir()
    selected = select_resume_source(
        str(source),
        auto_resume=False,
        restore_present=True,
        output_dir=str(output),
    )
    assert selected == source.resolve()


def test_select_explicit_relative_source_run_resolved(tmp_path: Path) -> None:
    selected = select_resume_source(
        "relative/prior",
        auto_resume=True,
        restore_present=True,
        output_dir=str(tmp_path),
    )
    assert selected is not None
    assert selected.is_absolute()


def test_select_auto_resume_picks_output_dir(tmp_path: Path) -> None:
    output = tmp_path / "this_run"
    output.mkdir()
    _write_checkpoint(output, step=2)
    selected = select_resume_source(
        None,
        auto_resume=True,
        restore_present=True,
        output_dir=str(output),
    )
    assert selected == output.resolve()


def test_select_no_resume_when_auto_resume_disabled(tmp_path: Path) -> None:
    output = tmp_path / "this_run"
    output.mkdir()
    _write_checkpoint(output, step=1)
    selected = select_resume_source(
        None,
        auto_resume=False,
        restore_present=True,
        output_dir=str(output),
    )
    assert selected is None


def test_select_no_resume_when_no_checkpoints(tmp_path: Path) -> None:
    # The common case: fresh Hydra output dir, auto_resume on, but nothing to resume.
    output = tmp_path / "fresh_run"
    output.mkdir()
    selected = select_resume_source(
        None,
        auto_resume=True,
        restore_present=True,
        output_dir=str(output),
    )
    assert selected is None


def test_select_no_resume_when_restore_absent(tmp_path: Path) -> None:
    output = tmp_path / "this_run"
    output.mkdir()
    _write_checkpoint(output, step=1)
    selected = select_resume_source(
        None,
        auto_resume=True,
        restore_present=False,
        output_dir=str(output),
    )
    assert selected is None
