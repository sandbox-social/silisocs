"""Filesystem-level contracts for Studio study composition and progress."""

from __future__ import annotations

from pathlib import Path

import yaml

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


def test_study_repository_rejects_unsafe_ids(tmp_path: Path) -> None:
    repository = StudyRepository(tmp_path)

    try:
        repository.save("../outside", yaml.safe_dump(_definition()))
    except ValueError as exc:
        assert "safe directory name" in str(exc)
    else:
        raise AssertionError("unsafe study id was accepted")
