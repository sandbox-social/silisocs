"""End-to-end study test: study.yaml → plan → run → repro lock → organized tree.

Uses the scripted (no-LLM) provider so the full study pipeline is exercised
without network access. This is the safety net for study-runner refactors.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.subprocess

REPO_ROOT = Path(__file__).resolve().parents[1]
STUDY_ID = "e2e_scripted_smoke"


def _study_payload(tmp_path: Path) -> dict:
    output_template = str(
        tmp_path / "runs" / "{hypothesis_id}" / "{condition_id}" / "{scenario}" / "seed_{seed}"
    )
    return {
        "schema_version": 1,
        "study": {
            "name": STUDY_ID,
            "study_id": STUDY_ID,
            "study_version": "v1",
            "question": "Does the study pipeline run end-to-end with a scripted model?",
            "scenarios": ["default"],
            "run_defaults": {
                "runner_module": "silisocs.runtime.runner",
                "run_name_template": "{study_id}_{hypothesis_id}_{condition_id}_seed{seed}",
                "output_root_override": output_template,
                "seeds": [1, 2],
                "overrides": {
                    "num_agents": 2,
                    "num_steps": 1,
                    "sim.llm.provider": "scripted",
                    "sim.llm.name": "scripted",
                    "env.gm.components.observe.params.timeline_mode": ("follower_chronological"),
                    "env.gm.components.update.built_in": "disabled",
                },
            },
        },
        "evaluations": [],
        "hypotheses": {
            "h1_smoke": {
                "statement": "The pipeline completes for every seed.",
                "independent_variable": "none",
                "prediction": "All runs succeed.",
                "status": "testing",
                "conditions": {
                    "baseline": {
                        "overrides": {},
                    },
                },
            },
        },
    }


def _run_study_subprocess(study_yaml: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["RUN_STUDY_PYTHON"] = sys.executable

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "silisocs.studies.run_study",
            "--study",
            str(study_yaml),
            "--repo-root",
            str(tmp_path),
            "run",
            "--max-concurrent",
            "2",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_study_runner_e2e_with_scripted_model(tmp_path: Path) -> None:
    study_dir = tmp_path / "study_def"
    study_dir.mkdir(parents=True)
    study_yaml = study_dir / "study.yaml"
    study_yaml.write_text(yaml.safe_dump(_study_payload(tmp_path)), encoding="utf-8")

    proc = _run_study_subprocess(study_yaml, tmp_path)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    generated = tmp_path / "experiments" / "studies" / STUDY_ID / "generated"
    lock_json = generated / "repro_lock.json"
    assert lock_json.is_file(), proc.stdout

    lock = json.loads(lock_json.read_text(encoding="utf-8"))

    # Environment provenance must be captured for reproducibility.
    environment = lock["environment"]
    assert environment["python_version"]
    assert environment["platform"]
    assert "git_commit" in environment
    assert "uv_lock_sha256" in environment

    # Both seed replicates ran and succeeded.
    records = lock["records"]
    assert len(records) == 2
    assert {record["status"] for record in records} == {"success"}
    assert {record["seed"] for record in records} == {1, 2}

    for record in records:
        run_dir = Path(record["run_dir"])
        assert run_dir.is_dir()
        assert (run_dir / "sim_metrics.json").is_file()
        assert record["lock"]["effective_config_sha256"]
        # Idempotent-resume marker is written after each successful run.
        marker = json.loads((run_dir / "RUN_COMPLETE.json").read_text(encoding="utf-8"))
        assert marker["run_id"] == record["run_id"]
        assert marker["return_code"] == 0
        assert marker["effective_config_sha256"] == record["lock"]["effective_config_sha256"]

    # The per-run jsonl lock and study index are also written.
    assert (generated / "repro_lock.jsonl").is_file()
    assert (generated / "study_index.json").is_file()

    # The organized analysis tree exists and contains both seed runs.
    organized = generated / "organized"
    assert organized.is_dir()
    assert (organized / "study_summary.yaml").is_file()
    seed_dirs = list(organized.rglob("seed_*"))
    assert len(seed_dirs) >= 2, sorted(str(p) for p in organized.rglob("*"))

    # Second invocation of the same study: every run resumes as
    # skipped_complete instead of re-executing, and prior run dirs survive.
    first_pass_run_dirs = {record["run_id"]: record["run_dir"] for record in records}
    proc2 = _run_study_subprocess(study_yaml, tmp_path)
    assert proc2.returncode == 0, f"stdout:\n{proc2.stdout}\nstderr:\n{proc2.stderr}"
    assert "Skipped 2 already-complete runs (use --force to re-run)" in proc2.stdout

    lock2 = json.loads(lock_json.read_text(encoding="utf-8"))
    records2 = lock2["records"]
    assert len(records2) == 2
    assert {record["status"] for record in records2} == {"skipped_complete"}
    for record in records2:
        first_run_dir = Path(first_pass_run_dirs[record["run_id"]]).resolve()
        assert Path(record["run_dir"]).resolve() == first_run_dir
        assert record["lock"]["effective_config_sha256"]
        assert Path(record["run_dir"], "sim_metrics.json").is_file()
