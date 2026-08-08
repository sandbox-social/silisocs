"""Tests for the ``silisocs`` CLI dispatch: help screens and clean config errors."""

from __future__ import annotations

import importlib
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.execution import session


def _run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    """Invoke :func:`cli_main` with ``args`` and Hydra's entrypoint booby-trapped."""

    def _explode() -> None:
        raise AssertionError("Hydra main must not run for this invocation")

    monkeypatch.setattr(session, "main", _explode)
    monkeypatch.setattr(sys, "argv", ["silisocs", *args])
    session.cli_main()


def _forbid_subcommand(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    """Make ``run_<command>`` fail loudly if the dispatch reaches it."""

    def _explode(*_args: Any, **_kwargs: Any) -> int:
        raise AssertionError(f"{command} must not run")

    monkeypatch.setattr(
        importlib.import_module(f"silisocs.runtime.{command}"), f"run_{command}", _explode
    )


@pytest.fixture
def empty_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run the test from an empty directory so stray writes are visible."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("flag", ["--help", "-h", "help"])
def test_top_level_help_prints_one_screen_without_hydra(
    flag: str, capsys, monkeypatch: pytest.MonkeyPatch, empty_cwd: Path
) -> None:
    _run_cli(monkeypatch, flag)

    out = capsys.readouterr().out
    assert "silisocs tutorial" in out  # golden path
    for subcommand in ("doctor", "tutorial", "new-scenario", "new-study"):
        assert subcommand in out
    for script in (
        "silisocs-studio",
        "silisocs-study",
        "silisocs-report",
        "silisocs-config-dry-run",
    ):
        assert script in out
    assert "--cfg job" in out  # how to see the composed config
    assert out.count("Usage:") == 1
    assert not list(empty_cwd.iterdir())


@pytest.mark.parametrize("command", ["doctor", "tutorial"])
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_subcommand_help_runs_nothing_and_writes_nothing(
    command: str, flag: str, capsys, monkeypatch: pytest.MonkeyPatch, empty_cwd: Path
) -> None:
    _forbid_subcommand(monkeypatch, command)

    _run_cli(monkeypatch, command, flag)

    out = capsys.readouterr().out
    assert out.startswith(f"usage: silisocs {command} [OUTPUT_DIR]")
    assert not list(empty_cwd.iterdir())


@pytest.mark.parametrize("command", ["doctor", "tutorial"])
def test_subcommand_rejects_flag_shaped_positional(
    command: str, monkeypatch: pytest.MonkeyPatch, empty_cwd: Path
) -> None:
    _forbid_subcommand(monkeypatch, command)

    with pytest.raises(SystemExit) as excinfo:
        _run_cli(monkeypatch, command, "--verbose")

    message = str(excinfo.value)
    assert "--verbose" in message
    assert f"usage: silisocs {command} [OUTPUT_DIR]" in message
    assert not list(empty_cwd.iterdir())


@pytest.mark.parametrize("command", ["doctor", "tutorial"])
def test_subcommand_rejects_extra_positional(
    command: str, monkeypatch: pytest.MonkeyPatch, empty_cwd: Path
) -> None:
    _forbid_subcommand(monkeypatch, command)

    with pytest.raises(SystemExit) as excinfo:
        _run_cli(monkeypatch, command, "one", "two")

    assert "'two'" in str(excinfo.value)


@pytest.mark.parametrize("command", ["doctor", "tutorial"])
def test_subcommand_passes_positional_through(
    command: str, monkeypatch: pytest.MonkeyPatch, empty_cwd: Path
) -> None:
    seen: list[str | None] = []

    def _record(target: str | None = None) -> int:
        seen.append(target)
        return 0

    monkeypatch.setattr(
        importlib.import_module(f"silisocs.runtime.{command}"), f"run_{command}", _record
    )

    with pytest.raises(SystemExit) as excinfo:
        _run_cli(monkeypatch, command, "runs/here")

    assert excinfo.value.code == 0
    assert seen == ["runs/here"]


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_scenario_gen_help_exits_cleanly(
    flag: str, capsys, monkeypatch: pytest.MonkeyPatch, empty_cwd: Path
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _run_cli(monkeypatch, "new-scenario", flag)

    assert excinfo.value.code == 0
    assert "--from-spec-json" in capsys.readouterr().out
    assert not list(empty_cwd.iterdir())


def test_hydra_overrides_still_reach_hydra_main(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(session, "main", lambda: called.append(True))
    monkeypatch.setattr(session, "_inject_external_config_path", lambda: None)
    monkeypatch.setattr(session, "_register_search_path_plugin", lambda: None)
    monkeypatch.setattr(sys, "argv", ["silisocs", "num_steps=1", "--cfg", "job"])

    session.cli_main()

    assert called == [True]


_VALIDATION_MESSAGE = "Configuration validation failed: Unsupported sim.llm.provider: 'bogus'."


def _raise_config_error(*_args: Any, **_kwargs: Any) -> None:
    raise session.ConfigValidationError(_VALIDATION_MESSAGE)


def test_config_validation_error_exits_cleanly(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HYDRA_FULL_ERROR", raising=False)
    monkeypatch.setattr(session, "run_simulation", _raise_config_error)

    with pytest.raises(SystemExit) as excinfo:
        session.main.__wrapped__(None)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.count(_VALIDATION_MESSAGE) == 1
    assert "Traceback" not in captured.err + captured.out


def test_config_validation_error_full_traceback_escape_hatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HYDRA_FULL_ERROR", "1")
    monkeypatch.setattr(session, "run_simulation", _raise_config_error)

    with pytest.raises(session.ConfigValidationError, match=r"Unsupported sim\.llm\.provider"):
        session.main.__wrapped__(None)


def test_internal_errors_are_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("a genuine bug")

    monkeypatch.setattr(session, "run_simulation", _raise)

    with pytest.raises(ValueError, match="a genuine bug"):
        session.main.__wrapped__(None)


class _StubMetrics:
    """Just enough of ``SimMetricsCollector`` for the validation phase."""

    @contextmanager
    def phase(self, _name: str) -> Iterator[None]:
        yield


def _validation_raises(exc: Exception) -> Any:
    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise exc

    return _raise


def _validate_only(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    monkeypatch.setattr(session, "validate_scenario_config", _validation_raises(exc))
    session._resolve_output_directory(
        OmegaConf.create({"scenario_name": "nonexistent"}),
        cast(Any, _StubMetrics()),
        logging.getLogger("test_cli_dispatch"),
    )


def test_rejected_config_is_wrapped_as_a_config_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(session.ConfigValidationError, match="Unsupported config key"):
        _validate_only(monkeypatch, ValueError("Unsupported config key(s) under sim: ['typo']"))


def test_missing_data_file_is_wrapped_as_a_config_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(session.ConfigValidationError, match="personas.json"):
        _validate_only(monkeypatch, FileNotFoundError("personas.json"))


def test_validator_internal_bug_keeps_its_own_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug inside a validator must not masquerade as a one-line config error."""
    bug = AttributeError("Key 'data' is not in struct")
    with pytest.raises(AttributeError, match="not in struct"):
        _validate_only(monkeypatch, bug)
