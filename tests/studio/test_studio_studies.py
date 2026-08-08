"""Filesystem-level contracts for Studio study composition and progress."""

from __future__ import annotations

from pathlib import Path

import yaml

from silisocs.studio.save_conflicts import SaveConflictError
from silisocs.studio.studies import (
    StudyRepository,
    compose_study,
    planned_run_count,
)


def _definition() -> dict:
    return {
        "schema_version": 1,
        "study": {
            "name": "General experiment",
            "id": "general_experiment",
            "scenarios": ["world_a", "world_b"],
            "run_defaults": {"seed_start": 3, "seed_repeats": 2},
        },
        "hypotheses": {
            "h1": {
                "statement": "Treatment changes the outcome",
                "custom_metadata": {"owner": "lab"},
                "conditions": {
                    "control": {"overrides": {}},
                    "treatment": {"overrides": {"world.treatment": True}},
                },
            }
        },
    }


def test_study_composition_preserves_unknown_fields() -> None:
    result = compose_study(
        yaml.safe_dump(_definition(), sort_keys=False),
        {"study.question": "Does treatment matter?"},
    )

    assert result["definition"]["study"]["question"] == "Does treatment matter?"
    assert result["definition"]["hypotheses"]["h1"]["custom_metadata"] == {"owner": "lab"}


def test_study_repository_validates_and_projects_board(tmp_path: Path) -> None:
    repository = StudyRepository(tmp_path)
    saved = repository.save(
        "general_experiment",
        yaml.safe_dump(_definition(), sort_keys=False),
    )

    assert planned_run_count(saved["definition"]) == 8
    assert len(saved["board"]) == 8
    assert {cell["seed"] for cell in saved["board"]} == {3, 4}
    assert {cell["scenario"] for cell in saved["board"]} == {"world_a", "world_b"}

    first = saved["board"][0]
    run_dir = Path(first["run_dir"])
    run_dir.mkdir(parents=True)
    (run_dir / "RUN_COMPLETE.json").write_text("{}\n", encoding="utf-8")

    refreshed = repository.load("general_experiment")
    assert sum(cell["status"] == "complete" for cell in refreshed["board"]) == 1


def test_study_save_writes_the_authors_text_verbatim(tmp_path: Path) -> None:
    """The definition is the author's file: comments and ordering survive a save."""
    repository = StudyRepository(tmp_path)
    authored = "# lab notebook: why this study exists\n" + yaml.safe_dump(
        _definition(), sort_keys=False
    )

    saved = repository.save("general_experiment", authored)

    on_disk = (tmp_path / "general_experiment" / "study.yaml").read_text(encoding="utf-8")
    assert on_disk == authored
    assert saved["yaml"] == authored
    assert saved["fingerprint"] == repository.load("general_experiment")["fingerprint"]


def test_study_save_refuses_a_stale_fingerprint(tmp_path: Path) -> None:
    """A second editor's save is refused rather than silently overwriting the first."""
    repository = StudyRepository(tmp_path)
    original = yaml.safe_dump(_definition(), sort_keys=False)
    repository.save("general_experiment", original)
    fingerprint = repository.load("general_experiment")["fingerprint"]

    ahead = "# another tab wrote this\n" + original
    repository.save("general_experiment", ahead, fingerprint=fingerprint)
    mine = original.replace("General experiment", "My experiment")

    try:
        repository.save("general_experiment", mine, fingerprint=fingerprint, baseline=original)
    except SaveConflictError as exc:
        assert exc.detail["file"] == "study.yaml"
        assert exc.detail["fingerprint"] == repository.load("general_experiment")["fingerprint"]
        assert "another tab wrote this" in exc.detail["diff"]
    else:
        raise AssertionError("a stale study save was accepted")

    path = tmp_path / "general_experiment" / "study.yaml"
    assert path.read_text(encoding="utf-8") == ahead
    # No fingerprint at all keeps the plain overwrite behaviour scripts rely on.
    repository.save("general_experiment", mine)
    assert path.read_text(encoding="utf-8") == mine


def test_study_repository_rejects_unsafe_ids(tmp_path: Path) -> None:
    repository = StudyRepository(tmp_path)

    try:
        repository.save("../outside", yaml.safe_dump(_definition()))
    except ValueError as exc:
        assert "safe directory name" in str(exc)
    else:
        raise AssertionError("unsafe study id was accepted")
