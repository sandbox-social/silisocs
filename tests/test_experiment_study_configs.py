"""Checks for release-facing experiment study definitions and generators."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from silisocs.studies.run_study import (
    StudyConfigError,
    _build_run_command,
    _build_submitit_job_commands,
    _expand_runs,
    _submitit_group_filters,
    validate_schema,
)
from silisocs.studies.study_artifacts import extract_run_metadata, load_study_definition

pytestmark = pytest.mark.subprocess

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STUDY_ROOT = PROJECT_ROOT / "experiments" / "studies"

REMOVED_OVERRIDE_KEYS = (
    "sim.timeline_mode",
    "sim.timeline_posts",
    "sim.timeline_config",
    "sim.gm.components.recommend",
    "sim.engine.action_loop",
    "sim.num_agents",
    "sim.num_steps",
    "sim.llm_name",
    "sim.enabled_actions",
    "sim.run_name",
    "sim.seed",
    "scenario.persona_pipeline",
    "scenario.probes",
    "query_data",
    "like_toot",
    "boost_toot",
)


def _active_study_files() -> list[Path]:
    return sorted(STUDY_ROOT.glob("*/study.yaml"))


def test_active_study_yaml_uses_current_runtime_keys() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _active_study_files():
        text = path.read_text(encoding="utf-8")
        found = [key for key in REMOVED_OVERRIDE_KEYS if key in text]
        if found:
            offenders[str(path.relative_to(PROJECT_ROOT))] = found

    assert offenders == {}


def test_active_study_yaml_expands_to_current_runner_commands() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _active_study_files():
        study = load_study_definition(path)
        runs, _, _ = _expand_runs(path, study)
        for spec in runs[:20]:
            command_text = " ".join(_build_run_command(spec))
            found = [key for key in REMOVED_OVERRIDE_KEYS if key in command_text]
            if found:
                offenders[f"{path.relative_to(PROJECT_ROOT)}::{spec.run_id}"] = found

    assert offenders == {}


def test_active_study_plan_command_runs_for_each_study(tmp_path: Path) -> None:
    for path in _active_study_files():
        plan_path = tmp_path / f"{path.parent.name}.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "silisocs.studies.run_study",
                "--study",
                str(path),
                "plan",
                "--output",
                str(plan_path),
            ],
            cwd=PROJECT_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert plan_path.is_file()


def test_slurm_templates_are_public_safe() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in (PROJECT_ROOT / "slurm_scripts").glob("*.sh")
    )
    forbidden = (
        "narval",
        "tamia",
        "ctb-",
        "aip-",
        "$SCRATCH",
        "VLLM_",
        "vllm",
        "Qwen",
        "qwen",
    )
    offenders = [term for term in forbidden if term in text]
    assert offenders == []


def test_slurm_array_exports_all_current_filters_and_hooks() -> None:
    script = PROJECT_ROOT / "slurm_scripts" / "study-array-template.sh"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "silisocs.studies.run_study",
            "--study",
            str(STUDY_ROOT / "study_template_v1" / "study.yaml"),
            "slurm-array",
            "--base-script",
            str(script),
            "--array-mode",
            "run",
            "--only-hypothesis",
            "h1_timeline_mechanism",
            "--only-condition",
            "case1_chronological",
            "--only-seed",
            "11",
            "--only-run-id",
            "h1_timeline_mechanism__case1_chronological__election_recsys_engagement__seed11",
            "--runner-python",
            "/usr/bin/python",
            "--setup-command",
            "echo setup",
            "--server-command",
            "echo server",
            "--server-ready-url",
            "http://127.0.0.1:8000/health",
            "--server-timeout-seconds",
            "42",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    stdout = result.stdout
    assert "HYPOTHESIS_IDS=h1_timeline_mechanism" in stdout
    assert "CONDITION_IDS=case1_chronological" in stdout
    assert "SEED_IDS=11" in stdout
    assert (
        "RUN_IDS=h1_timeline_mechanism__case1_chronological__election_recsys_engagement__seed11"
        in stdout
    )
    assert "RUNNER_PYTHON=/usr/bin/python" in stdout
    assert "SILISOCS_HPC_SETUP_COMMAND=echo setup" in stdout
    assert "SILISOCS_HPC_SERVER_COMMAND=echo server" in stdout
    assert "SILISOCS_HPC_SERVER_READY_URL=http://127.0.0.1:8000/health" in stdout
    assert "SILISOCS_HPC_SERVER_TIMEOUT_SECONDS=42" in stdout


def test_submitit_job_commands_use_current_filters() -> None:
    path = STUDY_ROOT / "study_template_v1" / "study.yaml"
    study = load_study_definition(path)
    runs, _, _ = _expand_runs(path, study)
    groups = _submitit_group_filters(runs, "run")
    commands = _build_submitit_job_commands(
        study_path=path,
        repo_root=PROJECT_ROOT,
        groups=groups[:2],
        max_concurrent=1,
        timeout_seconds=30,
    )

    assert commands
    command_text = "\n".join(" ".join(command) for command in commands)
    assert "--only-run-id" in command_text
    assert "seed=" not in command_text
    assert "sim.seed" not in command_text


def test_replication_graph_tool_emits_current_seed_override(tmp_path: Path) -> None:
    script = (
        PROJECT_ROOT
        / "replications"
        / "echo_chambers"
        / "tools"
        / "run_replication_graph_experiment.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo-root",
            str(PROJECT_ROOT),
            "--output-root",
            str(tmp_path / "graph"),
            "--network-types",
            "scale_free",
            "--runs",
            "1",
            "--num-agents",
            "50",
            "--network-seed",
            "50",
            "--dry-run",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "seed=50" in result.stdout
    assert "sim.seed" not in result.stdout


def test_study_template_exact_command_uses_current_runtime_keys() -> None:
    path = STUDY_ROOT / "study_template_v1" / "study.yaml"
    study = load_study_definition(path)
    runs, _, _ = _expand_runs(path, study)
    exact_run = next(spec for spec in runs if spec.condition_id == "case4_exact_command_mode")

    command = _build_run_command(exact_run)

    assert "seed=11" in command
    assert "num_steps=8" in command
    assert "env.gm.components.observe.params.timeline_mode=follower_chronological" in command
    assert all("sim.seed" not in token for token in command)
    assert all("sim.timeline_mode" not in token for token in command)


def test_study_schema_rejects_old_singular_evaluation() -> None:
    study = {
        "schema_version": 1,
        "study": {"name": "bad", "run_defaults": {"scenario": "default"}},
        "evaluation": {"preset": "builtin.activity_summary"},
        "hypotheses": {"h1": {"conditions": {"c1": {"overrides": {}}}}},
    }

    with pytest.raises(StudyConfigError, match="evaluations"):
        validate_schema(study)


def test_study_schema_rejects_old_cases_name() -> None:
    study = {
        "schema_version": 1,
        "study": {"name": "bad", "run_defaults": {"scenario": "default"}},
        "hypotheses": {"h1": {"cases": {"c1": {"overrides": {}}}}},
    }

    with pytest.raises(StudyConfigError, match="must define conditions"):
        validate_schema(study)


def test_study_artifact_metadata_reads_current_effective_config(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "effective_config.yaml").write_text(
        yaml.safe_dump(
            {
                "scenario_name": "resource_market",
                "num_steps": 3,
                "num_agents": 2,
                "seed": 99,
                "sim": {"llm": {"provider": "scripted", "name": "scripted"}},
                "setting": {"name": "market test"},
            }
        ),
        encoding="utf-8",
    )
    overrides_dir = run_dir / "configs" / "resource_market_001"
    overrides_dir.mkdir(parents=True)
    (overrides_dir / "overrides.yaml").write_text(
        yaml.safe_dump(["world=resource_market", "seed=99"]),
        encoding="utf-8",
    )

    metadata = extract_run_metadata(run_dir, config_path="scenarios/resource_market/conf")

    assert metadata["model_name"] == "scripted"
    assert metadata["model_config"] == "scripted"
    assert metadata["scenario"] == "resource_market"
    assert metadata["world_description"] == "market test"
    assert metadata["max_steps"] == 3
    assert metadata["num_agents"] == 2
    assert metadata["seed"] == 99
    assert metadata["cli_overrides"] == ["world=resource_market", "seed=99"]
    assert (
        metadata["run_command"] == "uv run python -m silisocs.runtime.runner --config-path "
        "scenarios/resource_market/conf world=resource_market seed=99"
    )
