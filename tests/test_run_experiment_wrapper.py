"""Tests for the legacy run_experiment.py wrapper."""

from __future__ import annotations

import subprocess
import sys

import pytest

import run_experiment


def _capture_command(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> list[str]:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], cwd) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sys, "argv", ["run_experiment.py", *args])
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        run_experiment.main()

    assert exc_info.value.code == 0
    return captured["cmd"]


def test_wrapper_emits_current_runner_override_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Study-style arguments should be translated to the current Hydra schema."""
    cmd = _capture_command(
        monkeypatch,
        [
            "scenario=neighborhood_forum",
            "model=gpt4omini",
            "simulation.execution.max_steps=3",
            "temperature=0.2",
        ],
    )

    assert "sim.llm.name=gpt-4o-mini" in cmd
    assert "num_steps=3" in cmd
    assert "sim.checkpoint.explicit_steps=[3]" in cmd
    assert "sim.llm.temperature=0.2" in cmd
    assert "sim.llm_name=gpt-4o-mini" not in cmd
    assert "sim.num_steps=3" not in cmd
    assert "sim.llm_temperature=0.2" not in cmd


def test_wrapper_maps_mock_model_to_disabled_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """The historical mock model alias should still select the no-op model path."""
    cmd = _capture_command(
        monkeypatch,
        [
            "scenario=neighborhood_forum",
            "model=mock",
        ],
    )

    assert "sim.llm.disabled=true" in cmd
    assert "sim.disable_language_model=true" not in cmd
