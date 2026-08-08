"""End-to-end checks that ``silisocs`` help and config errors stay side-effect free."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_RUNNER = [sys.executable, "-m", "silisocs.runtime.runner"]


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(_RUNNER + args, cwd=cwd, check=False, text=True, capture_output=True)


@pytest.mark.subprocess
@pytest.mark.parametrize("command", ["doctor", "tutorial", "new-scenario", "new-study"])
def test_subcommand_help_writes_nothing(command: str, tmp_path: Path) -> None:
    result = _run([command, "--help"], cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert command in result.stdout
    # The regression: --help used to be passed through as an output directory,
    # so the subcommand ran and produced ``./--help/``.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.subprocess
def test_top_level_help_writes_nothing_and_skips_hydra(tmp_path: Path) -> None:
    result = _run(["--help"], cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Subcommands:" in result.stdout
    assert "silisocs-studio" in result.stdout
    assert "num_agents" not in result.stdout  # no composed-config dump
    assert list(tmp_path.iterdir()) == []


@pytest.mark.subprocess
def test_config_error_prints_one_message_without_traceback(tmp_path: Path) -> None:
    result = _run(
        ["sim.llm.provider=bogus", f"hydra.run.dir={tmp_path / 'hydra'}"],
        cwd=tmp_path,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert combined.count("Unsupported sim.llm.provider: 'bogus'") == 1
    assert "Traceback" not in combined
    assert "HYDRA_FULL_ERROR" not in combined
