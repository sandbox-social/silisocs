"""Tests for CLI config-path searchpath injection behavior."""

import os
import sys

from silisocs.runtime.runner import _inject_external_config_path


def test_config_path_injects_hydra_searchpath_and_autodetects_sim_metadata(
    tmp_path, monkeypatch
) -> None:
    """Primary config-path should become searchpath and inject sim metadata overrides."""
    primary = tmp_path / "primary" / "conf"
    primary.mkdir(parents=True)
    (primary / "sim.yaml").write_text(
        "scenario_name: election\njobname_format: test_job_${sim.num_steps}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", ["runner.py", "--config-path", str(primary)])
    monkeypatch.delenv("MASTODON_SIM_EXTERNAL_CONFIG_DIRS", raising=False)
    _inject_external_config_path()

    assert "--config-path" not in sys.argv
    searchpath_arg = next(arg for arg in sys.argv if arg.startswith("hydra.searchpath="))
    assert f"file://{primary.resolve()}" in searchpath_arg
    assert "sim.scenario_name=election" in sys.argv
    assert any(arg.startswith("sim.jobname_format=") for arg in sys.argv)
    assert os.environ.get("MASTODON_SIM_EXTERNAL_CONFIG_DIRS") == str(primary.resolve())


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
