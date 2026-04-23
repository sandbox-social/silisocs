"""Tests for CLI config-path searchpath injection behavior."""

import sys

from mastodon_sim.runtime.runner import _inject_external_config_path


def test_config_path_injects_hydra_searchpath_and_autodetects_scenario(
    tmp_path, monkeypatch
) -> None:
    """Primary config-path should become a Hydra searchpath with scenario autodetect."""
    primary = tmp_path / "primary" / "conf"
    (primary / "scenario").mkdir(parents=True)
    (primary / "scenario" / "election.yaml").write_text(
        "scenario_name: election\n", encoding="utf-8"
    )

    monkeypatch.setattr(sys, "argv", ["runner.py", "--config-path", str(primary)])
    _inject_external_config_path()

    assert "--config-path" not in sys.argv
    searchpath_arg = next(arg for arg in sys.argv if arg.startswith("hydra.searchpath="))
    assert f"file://{primary.resolve()}" in searchpath_arg
    assert "scenario=election" in sys.argv


def test_overlay_config_paths_precede_primary_in_searchpath(tmp_path, monkeypatch) -> None:
    """Overlay config directories should take precedence over primary config-path."""
    primary = tmp_path / "primary" / "conf"
    overlay = tmp_path / "overlay" / "conf"
    (primary / "scenario").mkdir(parents=True)
    (overlay / "scenario").mkdir(parents=True)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner.py",
            "--config-path",
            str(primary),
            "--overlay-config-path",
            str(overlay),
        ],
    )
    _inject_external_config_path()

    searchpath_arg = next(arg for arg in sys.argv if arg.startswith("hydra.searchpath="))
    assert searchpath_arg.index(f"file://{overlay.resolve()}") < searchpath_arg.index(
        f"file://{primary.resolve()}"
    )
