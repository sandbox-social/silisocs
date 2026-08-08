"""Preflight cross-reference and data-file validation must actually run.

Both validators used to read ``persona_pipeline.classes`` at the config ROOT while
every scenario packages the pipeline at ``agents`` (``# @package agents``), and
guarded the rest with ``isinstance(node, dict)`` — false for a ``DictConfig``. The
net effect was two functions that printed a ✓ having checked nothing. These tests
pin the checks to the real composed shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.configuration.validation import (
    _UNIVERSAL_WORLD_PARAMS,
    validate_cross_references,
    validate_data_files,
)


def _cfg(**overrides: object) -> DictConfig:
    base: dict = {
        "agents": {
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "count": 2,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "sim_role_name": "voter",
                    },
                    "news": {
                        "count": 1,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "sim_role_name": "news_account",
                    },
                }
            }
        },
        "env": {"gm": {"components": {"initialize": {"params": {"graph": {}}}}}},
    }
    base.update(overrides)  # type: ignore[arg-type]
    return OmegaConf.create(base)


def _set_targets(cfg: DictConfig, targets: list[str]) -> DictConfig:
    OmegaConf.update(
        cfg, "env.gm.components.initialize.params.graph.fully_connected_targets", targets
    )
    return cfg


# ----------------------------------------------------------- follow-graph roles


def test_unknown_fully_connected_target_role_raises() -> None:
    """A target naming a persona CLASS instead of its sim role must fail."""
    cfg = _set_targets(_cfg(), ["voter", "news"])  # `news` is the class; role is news_account
    with pytest.raises(ValueError, match="unknown sim role 'news'"):
        validate_cross_references(cfg)


def test_known_fully_connected_target_roles_pass() -> None:
    validate_cross_references(_set_targets(_cfg(), ["voter", "news_account"]))


def test_fully_connected_targets_checked_inside_gm_orchestration() -> None:
    """Per-GM orchestration blocks obey the same rule as the single env.gm block."""
    cfg = _cfg()
    OmegaConf.update(
        cfg,
        "env.gm_orchestration.gms",
        [
            {
                "gm_name": "second",
                "components": {
                    "initialize": {"params": {"graph": {"fully_connected_targets": ["typo"]}}}
                },
            }
        ],
    )
    with pytest.raises(ValueError, match="unknown sim role 'typo'"):
        validate_cross_references(cfg)


def test_role_check_uses_class_name_when_sim_role_name_is_absent() -> None:
    cfg = OmegaConf.create(
        {
            "agents": {
                "persona_pipeline": {
                    "classes": {"hobbyist": {"class_path": "silisocs.agents.native.NativeAgent"}}
                }
            }
        }
    )
    validate_cross_references(_set_targets(cfg, ["hobbyist"]))
    with pytest.raises(ValueError, match="unknown sim role 'hobbyst'"):
        validate_cross_references(_set_targets(cfg, ["hobbyst"]))


# --------------------------------------------------------- fixed-action set refs


def test_bogus_inline_action_set_ref_raises() -> None:
    cfg = OmegaConf.create(
        {
            "agents": {
                "fixed_action_sets": {"inline": {"news_actions": {"actions": []}}},
                "persona_pipeline": {
                    "classes": {
                        "news": {
                            "class_path": "silisocs.agents.fixed.FixedAgent",
                            "fixed_action": {"enabled": True, "action_set_ref": "new_actions"},
                        }
                    }
                },
            }
        }
    )
    with pytest.raises(ValueError, match="unknown fixed-action set `new_actions`"):
        validate_cross_references(cfg)


def test_enabled_fixed_action_without_ref_raises() -> None:
    cfg = OmegaConf.create(
        {
            "agents": {
                "persona_pipeline": {
                    "classes": {
                        "news": {
                            "class_path": "silisocs.agents.fixed.FixedAgent",
                            "fixed_action": {"enabled": True},
                        }
                    }
                }
            }
        }
    )
    with pytest.raises(ValueError, match="no action_set_ref"):
        validate_cross_references(cfg)


def test_known_inline_action_set_ref_passes() -> None:
    cfg = OmegaConf.create(
        {
            "agents": {
                "fixed_action_sets": {"inline": {"news_actions": {"actions": []}}},
                "persona_pipeline": {
                    "classes": {
                        "news": {
                            "class_path": "silisocs.agents.fixed.FixedAgent",
                            "fixed_action": {"enabled": True, "action_set_ref": "news_actions"},
                        }
                    }
                },
            }
        }
    )
    validate_cross_references(cfg)


# ---------------------------------------------------------------- probe entries


def test_probe_entry_without_probe_type_raises() -> None:
    cfg = _cfg()
    OmegaConf.update(cfg, "eval.probes.probes", {"vote": {"probe_data": {"name": "Vote"}}})
    with pytest.raises(ValueError, match="must declare a `probe_type`"):
        validate_cross_references(cfg)


# ------------------------------------------------------------------ data files


def _pipeline_cfg(class_cfg: dict) -> DictConfig:
    return OmegaConf.create({"agents": {"persona_pipeline": {"classes": {"user": class_cfg}}}})


def test_typo_in_class_data_path_fails_preflight(tmp_path: Path) -> None:
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "personas.json").write_text(json.dumps([{"name": "A"}]))
    cfg = _pipeline_cfg({"data": {"source": "local_json", "path": "personas.jsno"}})
    with pytest.raises(FileNotFoundError, match="personas.jsno"):
        validate_data_files(cfg, tmp_path)
    # The correct spelling resolves through the scenario input/ search path.
    validate_data_files(
        _pipeline_cfg({"data": {"source": "local_json", "path": "personas.json"}}), tmp_path
    )


def test_typo_in_shared_memories_path_fails_preflight(tmp_path: Path) -> None:
    cfg = _pipeline_cfg({"shared_memories": {"path": "memories.txt"}})
    with pytest.raises(FileNotFoundError, match="shared_memories.path not found"):
        validate_data_files(cfg, tmp_path)


def test_class_pipeline_does_not_trip_the_legacy_persona_file_branch(tmp_path: Path) -> None:
    """A class-pipeline scenario must not be validated as a legacy persona_file one."""
    cfg = _pipeline_cfg({"data": {"source": "inline", "records": [{"name": "A"}]}})
    OmegaConf.update(cfg, "data.persona_file", "does_not_exist.json", force_add=True)
    validate_data_files(cfg, tmp_path)


def test_legacy_persona_file_still_validated_without_a_pipeline(tmp_path: Path) -> None:
    cfg = OmegaConf.create({"data": {"persona_file": "missing.json"}})
    with pytest.raises(FileNotFoundError, match="missing.json"):
        validate_data_files(cfg, tmp_path)


# ------------------------------------------------- world-params copy-paste block


def test_universal_world_params_suggest_an_empty_output_dir() -> None:
    """`output_dir: outputs` would collapse every run into one overwriting dir."""
    output_dir_lines = [line for line in _UNIVERSAL_WORLD_PARAMS if line.startswith("output_dir")]
    assert output_dir_lines, "the guidance block must still declare output_dir"
    assert output_dir_lines[0].startswith('output_dir: ""')
