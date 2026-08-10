"""``summary-append`` writes into the same study tree ``run`` generates into.

Regression guard for a study kept OUTSIDE the repo root: SUMMARY.md and
generated/summary_log.jsonl must resolve from the shared workspace helper, not
from a second, independently derived layout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from silisocs.studies.cli import build_parser, cmd_summary_append
from silisocs.studies.plan import (
    resolve_summary_paths,
    study_generated_dir,
    study_workspace_dir,
)


def _study_data(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    study: dict[str, Any] = {
        "name": "out_of_tree",
        "study_id": "out_of_tree",
        "run_defaults": {"scenario": "default", "seeds": [1]},
    }
    study.update(extra or {})
    return {
        "schema_version": 1,
        "study": study,
        "hypotheses": {"h1": {"conditions": {"c1": {"overrides": {}}}}},
    }


def _write_out_of_tree_study(tmp_path: Path, extra: dict[str, Any] | None = None) -> Path:
    study_dir = tmp_path / "outside" / "my_study"
    study_dir.mkdir(parents=True)
    path = study_dir / "study.yaml"
    path.write_text(yaml.safe_dump(_study_data(extra), sort_keys=False), encoding="utf-8")
    return path


def _summary_args(study_path: Path, repo_root: Path) -> Any:
    return build_parser().parse_args(
        [
            "--study",
            str(study_path),
            "--repo-root",
            str(repo_root),
            "summary-append",
            "--author",
            "tester",
            "--note",
            "first look",
        ]
    )


def test_summary_paths_share_the_generated_root_run_uses(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    study_data = _study_data()

    summary_md, summary_log = resolve_summary_paths(repo_root, study_data)
    generated = study_generated_dir(repo_root, study_data["study"])

    assert summary_log.parent == generated
    assert summary_md.parent == study_workspace_dir(repo_root, study_data["study"])
    assert summary_md.parent == generated.parent


def test_summary_append_writes_into_the_run_tree_for_an_out_of_tree_study(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    study_path = _write_out_of_tree_study(tmp_path)

    rc = cmd_summary_append(_summary_args(study_path, repo_root))
    captured = capsys.readouterr()

    generated = repo_root / "experiments" / "studies" / "out_of_tree" / "generated"
    assert rc == 0
    assert (generated / "summary_log.jsonl").is_file()
    assert (generated.parent / "SUMMARY.md").is_file()
    # No second generated/ tree next to the study definition.
    assert not (study_path.parent / "generated").exists()
    assert "⚠" not in captured.err

    entry = json.loads((generated / "summary_log.jsonl").read_text(encoding="utf-8").strip())
    assert entry["study_id"] == "out_of_tree"
    assert entry["author"] == "tester"


def test_declared_paths_outside_the_workspace_are_flagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    study_path = _write_out_of_tree_study(
        tmp_path,
        {
            "study_summary_path": "elsewhere/SUMMARY.md",
            "summary_log_path": "elsewhere/generated/summary_log.jsonl",
        },
    )

    rc = cmd_summary_append(_summary_args(study_path, repo_root))
    captured = capsys.readouterr()

    assert rc == 0
    assert (repo_root / "elsewhere" / "generated" / "summary_log.jsonl").is_file()
    assert captured.err.count("outside this study's workspace") == 2
    assert "study.summary_log_path" in captured.err
