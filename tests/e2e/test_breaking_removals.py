from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from silisocs.agents import base_agent
from silisocs.initialization.agents import formative

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_legacy_study_runner_module_is_removed() -> None:
    assert not (PROJECT_ROOT / "experiments" / "run_study.py").exists()
    assert importlib.util.find_spec("experiments.run_study") is None


def test_legacy_agent_alias_is_removed() -> None:
    assert not hasattr(base_agent, "AgentLike")


def test_legacy_formative_initializer_alias_is_removed() -> None:
    assert not hasattr(formative, "FormativeMemoryInitializer")


def test_legacy_study_runner_import_fails() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("experiments.run_study")
