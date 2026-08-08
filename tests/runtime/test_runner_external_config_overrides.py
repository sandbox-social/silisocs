"""Tests for CLI config-path searchpath injection behavior."""

import os
import sys

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.config_dry_run import DryRunTarget, _build_command
from silisocs.runtime.configuration.external import merge_external_group_overrides
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
    env_dirs = os.environ.get("SILISOCS_EXTERNAL_CONFIG_DIRS", "").split(os.pathsep)
    assert env_dirs[0] == str(primary.resolve())
    assert env_dirs[1] == str(overlay.resolve())


def test_external_group_merge_includes_agents_yaml(tmp_path, monkeypatch) -> None:
    """External overlays can override benchmark/generated agent records."""
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "agents.yaml").write_text(
        """
persona_pipeline:
  classes:
    user:
      count: 1
      data:
        source: inline
        records:
          - name: user_1
            persona: Benchmark-specific persona.
      field_map:
        name: name
        context: persona
""",
        encoding="utf-8",
    )
    cfg = OmegaConf.create(
        {
            "agents": {
                "persona_pipeline": {
                    "classes": {
                        "user": {
                            "count": 5,
                            "data": {
                                "source": "inline",
                                "records": [{"persona": "Packaged persona."}],
                            },
                        }
                    }
                }
            }
        }
    )
    monkeypatch.setenv("SILISOCS_EXTERNAL_CONFIG_DIRS", str(overlay))

    merged = merge_external_group_overrides(cfg)

    user_cfg = merged.agents.persona_pipeline.classes.user
    assert user_cfg.count == 1
    assert user_cfg.data.records[0].name == "user_1"
    assert user_cfg.data.records[0].persona == "Benchmark-specific persona."


def test_config_dry_run_selects_matching_external_agent_and_env_groups(tmp_path) -> None:
    conf_dir = tmp_path / "world" / "conf"
    (conf_dir / "agents").mkdir(parents=True)
    (conf_dir / "env").mkdir()
    (conf_dir / "agents" / "resource_market.yaml").write_text("{}\n", encoding="utf-8")
    (conf_dir / "env" / "resource_market.yaml").write_text("{}\n", encoding="utf-8")
    target = DryRunTarget(
        label="world/resource_market",
        config_path=conf_dir,
        world_variant="resource_market",
    )

    command = _build_command(target, output_dir=tmp_path / "out", hydra_dir=tmp_path / "hydra")

    assert "agents=resource_market" in command
    assert "env=resource_market" in command
    assert "++sim.llm.provider=scripted" in command
    assert "++sim.llm.name=scripted" in command
    assert "++sim.llm.disabled=true" in command


def test_malformed_override_fails_instead_of_being_silently_dropped(tmp_path, monkeypatch) -> None:
    """An override with no `=` cannot be re-applied over the scenario's flat groups.

    Dropping it would run with the scenario's value while the user believes their
    override took effect, so it must fail the run here.
    """
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "sim.yaml").write_text("llm:\n  temperature: 0.1\n", encoding="utf-8")
    monkeypatch.setenv("SILISOCS_EXTERNAL_CONFIG_DIRS", str(overlay))
    cfg = OmegaConf.create({"sim": {"llm": {"temperature": 0.7}}})

    with pytest.raises(ValueError, match="Malformed override"):
        merge_external_group_overrides(cfg, value_overrides=["sim.llm.temperature"])

    # Well-formed overrides still win over the merged flat group file.
    merged = merge_external_group_overrides(cfg, value_overrides=["sim.llm.temperature=0.9"])
    assert merged.sim.llm.temperature == 0.9
