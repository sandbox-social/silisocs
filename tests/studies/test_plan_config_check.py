"""``silisocs-study plan`` must compose every unique condition config.

A grid whose overrides do not compose plans cleanly but loses 100% of its runs
at Hydra composition; the plan-time check turns that into a loud, actionable
failure before anything is launched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from silisocs.studies.cli import build_parser, cmd_plan
from silisocs.studies.plan import (
    expand_runs,
    unique_condition_configs,
    validate_condition_configs,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _study_data(conditions: dict[str, dict[str, Any]], seeds: list[int]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "study": {
            "name": "compose_check",
            "study_id": "compose_check",
            "run_defaults": {
                "scenario": "default",
                "seeds": seeds,
                "checkpoint_every_n_steps": None,
            },
        },
        "hypotheses": {
            "h1": {"conditions": {cond: {"overrides": ov} for cond, ov in conditions.items()}}
        },
    }


def _specs(tmp_path: Path, conditions: dict[str, dict[str, Any]], seeds: list[int]) -> list[Any]:
    run_specs, _, _ = expand_runs(tmp_path / "study.yaml", _study_data(conditions, seeds))
    return run_specs


def test_unique_condition_configs_dedups_replicate_seeds(tmp_path: Path) -> None:
    run_specs = _specs(tmp_path, {"c1": {"num_steps": 1}, "c2": {"num_steps": 2}}, [1, 2, 3])

    assert len(run_specs) == 6
    assert [spec.condition_id for spec in unique_condition_configs(run_specs)] == ["c1", "c2"]


def test_valid_overrides_compose(tmp_path: Path) -> None:
    run_specs = _specs(tmp_path, {"c1": {"num_steps": 1, "sim.llm.provider": "scripted"}}, [1])

    assert validate_condition_configs(run_specs, PROJECT_ROOT) == []


def test_slot_param_override_without_plus_prefix_is_reported(tmp_path: Path) -> None:
    # Base slot params are `{}`, so a bare dotted param override cannot be
    # applied — the exact class of failure that used to plan cleanly and then
    # kill every run.
    run_specs = _specs(tmp_path, {"c1": {"sim.engine.turn_policy.params.max_actions": 6}}, [1])

    failures = validate_condition_configs(run_specs, PROJECT_ROOT)

    assert len(failures) == 1
    assert failures[0]["condition"] == "c1"
    assert "sim.engine.turn_policy.params.max_actions" in failures[0]["error"]
    assert "sim.engine.turn_policy.params.max_actions=6" in failures[0]["overrides"]


def test_plus_prefixed_override_composes(tmp_path: Path) -> None:
    run_specs = _specs(tmp_path, {"c1": {"++sim.engine.turn_policy.params.max_actions": 6}}, [1])

    assert validate_condition_configs(run_specs, PROJECT_ROOT) == []


def _write_study(tmp_path: Path, conditions: dict[str, dict[str, Any]]) -> Path:
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(_study_data(conditions, [1]), sort_keys=False), encoding="utf-8")
    return path


def _plan_args(study_path: Path, *extra: str) -> Any:
    return build_parser().parse_args(
        ["--study", str(study_path), "--repo-root", str(PROJECT_ROOT), "plan", *extra]
    )


def test_plan_exits_nonzero_and_names_the_failing_condition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_path = _write_study(
        tmp_path,
        {"good": {"num_steps": 1}, "bad": {"sim.engine.turn_policy.params.max_actions": 6}},
    )

    rc = cmd_plan(_plan_args(study_path))
    captured = capsys.readouterr()

    assert rc == 1
    assert "Condition config check: 1/2" in captured.out
    assert "h1/bad" in captured.err
    assert "sim.engine.turn_policy.params.max_actions" in captured.err
    assert "h1/good" not in captured.err


def test_plan_passes_when_every_condition_composes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_path = _write_study(tmp_path, {"good": {"num_steps": 1}})

    rc = cmd_plan(_plan_args(study_path))

    assert rc == 0
    assert "Condition config check: 1/1" in capsys.readouterr().out


def test_plan_can_skip_the_config_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    study_path = _write_study(tmp_path, {"bad": {"sim.engine.turn_policy.params.max_actions": 6}})

    rc = cmd_plan(_plan_args(study_path, "--skip-config-check"))

    assert rc == 0
    assert "Condition config check: skipped" in capsys.readouterr().out
