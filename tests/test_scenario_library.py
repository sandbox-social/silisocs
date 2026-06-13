"""Scenario library resolution (repo checkout and packaged installs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from silisocs.scenario_library import list_scenarios, scenario_conf_path, scenarios_root

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_checkout_resolves_scenarios_root() -> None:
    assert scenarios_root() == REPO_ROOT / "scenarios"


def test_list_scenarios_contains_bundled_set() -> None:
    names = list_scenarios()
    assert "election" in names
    assert "misinformation" in names


@pytest.mark.parametrize(
    "reference",
    ["election", "scenarios/election", "scenarios/election/conf", "election/conf"],
)
def test_scenario_conf_path_accepts_all_documented_forms(reference: str) -> None:
    assert scenario_conf_path(reference) == REPO_ROOT / "scenarios" / "election" / "conf"


def test_unknown_scenario_lists_available() -> None:
    with pytest.raises(FileNotFoundError, match="Available scenarios"):
        scenario_conf_path("does_not_exist")


def test_config_path_flag_accepts_bare_scenario_name(monkeypatch) -> None:
    import sys

    from silisocs.runtime.configuration.external import inject_external_config_path

    monkeypatch.setattr(sys, "argv", ["runner.py", "--config-path", "election"])
    monkeypatch.delenv("SILISOCS_EXTERNAL_CONFIG_DIRS", raising=False)
    inject_external_config_path()
    import os

    assert os.environ["SILISOCS_EXTERNAL_CONFIG_DIRS"] == str(
        REPO_ROOT / "scenarios" / "election" / "conf"
    )
