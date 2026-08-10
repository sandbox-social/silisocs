"""Evaluator commands must use the SAME interpreter the study launches runs with.

A hard-coded ``uv run python`` prefix makes every evaluator die with
ModuleNotFoundError in a pip-installed (non-uv) environment, while the runs
themselves succeed under ``sys.executable``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from silisocs.studies.evaluation_presets import BUILTIN_EVAL_PRESETS, PYTHON_TOKEN
from silisocs.studies.plan import build_run_command, expand_runs, resolve_runner_python


def _study_data(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "study": {
            "name": "eval_python",
            "study_id": "eval_python",
            "run_defaults": {"scenario": "default", "seeds": [1]},
        },
        "evaluations": evaluations,
        "hypotheses": {"h1": {"conditions": {"c1": {"overrides": {}}}}},
    }


def _eval_command(tmp_path: Path, evaluations: list[dict[str, Any]]) -> tuple[str, ...]:
    _, eval_specs, _ = expand_runs(tmp_path / "study.yaml", _study_data(evaluations))
    assert len(eval_specs) == 1
    return eval_specs[0].command


def test_no_builtin_preset_hardcodes_a_launcher() -> None:
    for preset_id, preset in BUILTIN_EVAL_PRESETS.items():
        assert preset["command"][0] == PYTHON_TOKEN, preset_id
        assert "uv" not in preset["command"], preset_id


@pytest.mark.parametrize("preset_id", sorted(BUILTIN_EVAL_PRESETS))
def test_preset_command_starts_with_the_resolved_interpreter(
    tmp_path: Path, preset_id: str
) -> None:
    if preset_id == "builtin.study_eval":
        # The only preset with a "./eval.py" script token: it must exist.
        (tmp_path / "eval.py").write_text("", encoding="utf-8")

    command = _eval_command(tmp_path, [{"id": "e1", "preset": preset_id}])

    assert command[0] == sys.executable
    assert command[0] == resolve_runner_python()
    assert "uv" not in command


def test_evaluator_and_run_share_one_interpreter(tmp_path: Path) -> None:
    run_specs, eval_specs, _ = expand_runs(
        tmp_path / "study.yaml",
        _study_data([{"id": "e1", "preset": "builtin.action_metrics_detailed"}]),
    )

    assert eval_specs[0].command[0] == build_run_command(run_specs[0])[0]


def test_run_study_python_env_var_applies_to_evaluators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUN_STUDY_PYTHON", "/opt/custom/bin/python")

    command = _eval_command(tmp_path, [{"id": "e1", "preset": "builtin.probe_metrics_detailed"}])

    assert command[0] == "/opt/custom/bin/python"


def test_custom_command_may_use_the_python_token(tmp_path: Path) -> None:
    command = _eval_command(
        tmp_path, [{"id": "e1", "command": [PYTHON_TOKEN, "-m", "my_pkg.evaluator"]}]
    )

    assert command == (sys.executable, "-m", "my_pkg.evaluator")


def test_custom_command_without_the_token_is_untouched(tmp_path: Path) -> None:
    command = _eval_command(tmp_path, [{"id": "e1", "command": ["Rscript", "analyse.R"]}])

    assert command == ("Rscript", "analyse.R")


def test_study_eval_preset_resolves_the_script_next_to_the_interpreter(tmp_path: Path) -> None:
    (tmp_path / "eval.py").write_text("", encoding="utf-8")

    command = _eval_command(tmp_path, [{"id": "e1", "preset": "builtin.study_eval"}])

    assert command == (sys.executable, str(tmp_path / "eval.py"))
