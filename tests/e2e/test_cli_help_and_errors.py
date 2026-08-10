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


def test_report_cli_reports_its_missing_extra_instead_of_a_traceback(monkeypatch, capsys) -> None:
    """`silisocs-report` on a lean install must read like `silisocs-studio` does.

    jinja2 ships in the `analysis` extra and was imported at module scope, so the
    console script died on a raw ModuleNotFoundError traceback.
    """
    from silisocs.analysis import report

    class _NoJinja:
        @staticmethod
        def import_module(name: str) -> object:
            raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr(report, "importlib", _NoJinja)
    monkeypatch.setattr(sys, "argv", ["silisocs-report", "some/run"])

    assert report.missing_analysis_extra() == (
        'silisocs-report requires: pip install "silisocs[analysis]"'
    )
    assert report.main() == 1
    out = capsys.readouterr().out
    assert out.strip() == 'silisocs-report requires: pip install "silisocs[analysis]"'


def test_report_cli_guard_is_silent_when_the_extra_is_installed() -> None:
    from silisocs.analysis import report

    assert report.missing_analysis_extra() is None


@pytest.mark.subprocess
def test_disabled_llm_under_packaged_tool_calling_fails_at_build(tmp_path: Path) -> None:
    """The flag `silisocs doctor` used to recommend produced a 100%-degraded run.

    The packaged default is `sim.tool_calling.mode: single`, and the no-op model
    answers a tool-call spec with an empty list, so every agent turn failed while
    the run reported success. It is now a build-time config error.
    """
    result = _run(
        ["sim.llm.disabled=true", f"hydra.run.dir={tmp_path / 'hydra'}"],
        cwd=tmp_path,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert "sim.llm.disabled=true cannot be combined with sim.tool_calling.mode" in combined
    assert "sim.llm.provider=scripted" in combined
    assert "Traceback" not in combined


@pytest.mark.subprocess
def test_root_level_probes_block_fails_at_build(tmp_path: Path) -> None:
    """A `probes:` block at the config root is read by nothing — say so, loudly."""
    result = _run(
        [
            "+probes.probes.mine.probe_type=BinaryProbe",
            f"hydra.run.dir={tmp_path / 'hydra'}",
        ],
        cwd=tmp_path,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert "root-level `probes:` block" in combined
    assert "conf/eval.yaml" in combined
