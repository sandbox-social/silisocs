import pytest
from concordia.typing import prefab as prefab_lib
from omegaconf import OmegaConf

pytest.importorskip("psutil")

from silisocs.runtime.runner import build_game_masters


def _base_cfg(processing_mode: str):
    return OmegaConf.create(
        {
            "sim": {
                "action_mode": "custom",
                "tool_calling": {"mode": "none"},
                "prompt_additions": {"action_count_guidance": True},
            },
            "agents": {
                "shared_memories": ["shared memory"],
                "persona_pipeline": {"processing_mode": processing_mode},
            },
            "env": {
                "gm": {"components": {"resolve": {"built_in": "parsed_action"}}},
                "social_network": {
                    "activity_transition_rates": {
                        "user": {
                            "inactive_to_active": 0.3,
                            "active_to_inactive": 0.3,
                        }
                    },
                    "fully_connected_targets": [],
                    "base_followership_probability": 0.3,
                },
                "usage_instructions": "Use platform respectfully.",
                "action_prompt": "Act on timeline.",
                "gamemaster": {
                    "name": "social-media_game-master",
                    "filename": "social_media_game_master",
                    "sim_role": {
                        "name": "social_media_gm",
                        "module_path": "silisocs.environments.gm.game_master",
                    },
                },
            },
        }
    )


def test_build_game_masters_formative_mode() -> None:
    cfg = _base_cfg("formative")
    game_masters = build_game_masters(cfg)

    initializer = game_masters[0]
    assert initializer.prefab == "formative_memories_initializer__GameMaster"
    assert initializer.params["module_path"] == "silisocs.agents.initialization.formative"


def test_build_game_masters_raw_mode() -> None:
    cfg = _base_cfg("raw")
    game_masters = build_game_masters(cfg)

    initializer = game_masters[0]
    assert initializer.prefab == "raw_memories_initializer__GameMaster"
    assert initializer.params["module_path"] == "silisocs.agents.initialization.raw"


def test_build_game_masters_supports_per_gm_prompt_overrides() -> None:
    cfg = _base_cfg("raw")
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm_alpha",
                "sequence": 0,
                "sim_role": {
                    "name": "social_media_gm",
                    "module_path": "silisocs.environments.gm.game_master",
                },
                "prompt": {
                    "action_prompt": "Alpha prompt body\n[OUTPUT STYLE]",
                    "output_style": "Alpha output style",
                },
            },
            {
                "gm_name": "gm_beta",
                "sequence": 1,
                "sim_role": {
                    "name": "social_media_gm",
                    "module_path": "silisocs.environments.gm.game_master",
                },
                "prompt": {
                    "action_prompt": "Beta prompt body\n[OUTPUT STYLE]",
                    "output_style": "Beta output style",
                },
            },
        ],
        "flow_bindings": {
            "flow_to_gm": {},
            "flow_to_gms": {},
            "gm_to_flows": {},
        },
    }

    game_masters = build_game_masters(cfg)
    social_media_gms = [gm for gm in game_masters if gm.role == prefab_lib.Role.GAME_MASTER]
    prompts_by_name = {
        gm.params["name"]: gm.params["calls_to_action"]["social_media_action"]
        for gm in social_media_gms
    }

    assert "gm_alpha" in prompts_by_name
    assert "gm_beta" in prompts_by_name
    assert "Alpha prompt body" in prompts_by_name["gm_alpha"]
    assert "Alpha output style" in prompts_by_name["gm_alpha"]
    assert "Beta prompt body" in prompts_by_name["gm_beta"]
    assert "Beta output style" in prompts_by_name["gm_beta"]
