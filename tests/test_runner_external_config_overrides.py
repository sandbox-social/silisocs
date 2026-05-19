"""Tests for CLI config-path searchpath injection behavior."""

import os
import sys

from silisocs.runtime.execution.session import _inject_external_config_path


def test_config_path_sets_env_var_and_strips_flag(tmp_path, monkeypatch) -> None:
    """--config-path should be stripped from argv and set SILISOCS_EXTERNAL_CONFIG_DIRS."""
    primary = tmp_path / "primary" / "conf"
    primary.mkdir(parents=True)

    monkeypatch.setattr(sys, "argv", ["runner.py", "--config-path", str(primary)])
    monkeypatch.delenv("SILISOCS_EXTERNAL_CONFIG_DIRS", raising=False)
    _inject_external_config_path()

    assert "--config-path" not in sys.argv
    assert os.environ.get("SILISOCS_EXTERNAL_CONFIG_DIRS") == str(primary.resolve())


def test_overlay_config_paths_precede_primary_in_env_var(tmp_path, monkeypatch) -> None:
    """Overlay config directories should appear before primary in SILISOCS_EXTERNAL_CONFIG_DIRS."""
    primary = tmp_path / "primary" / "conf"
    overlay = tmp_path / "overlay" / "conf"
    primary.mkdir(parents=True)
    overlay.mkdir(parents=True)

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
    monkeypatch.delenv("SILISOCS_EXTERNAL_CONFIG_DIRS", raising=False)
    _inject_external_config_path()

    assert "--config-path" not in sys.argv
    assert "--overlay-config-path" not in sys.argv
    env_dirs = os.environ.get("SILISOCS_EXTERNAL_CONFIG_DIRS", "").split(":")
    assert env_dirs[0] == str(primary.resolve())
    assert env_dirs[1] == str(overlay.resolve())
