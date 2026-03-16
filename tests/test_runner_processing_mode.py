import pytest
from omegaconf import OmegaConf

pytest.importorskip("psutil")

from mastodon_sim.runtime.runner import build_game_masters


def _base_cfg(processing_mode: str):
    return OmegaConf.create(
        {
            "scenario": {
                "shared_memories": ["shared memory"],
                "persona_pipeline": {"processing_mode": processing_mode},
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
            },
            "social_media": {
                "usage_instructions": "Use platform respectfully.",
                "action_call_to_action": "Act on timeline.",
                "gamemaster": {
                    "name": "social-media_game-master",
                    "filename": "social_media_game_master",
                    "sim_role": {
                        "name": "social_media_gm",
                        "module_path": "mastodon_sim.environments.gm.game_master",
                    },
                },
            },
        }
    )


def test_build_game_masters_accepts_formative_alias() -> None:
    cfg = _base_cfg("formative")
    game_masters = build_game_masters(cfg)

    initializer = game_masters[0]
    assert initializer.prefab == "formative_memories_initializer__GameMaster"
    assert initializer.params["module_path"] == "mastodon_sim.agents.initialization.formative"


def test_build_game_masters_accepts_llm_formative() -> None:
    cfg = _base_cfg("llm_formative")
    game_masters = build_game_masters(cfg)

    initializer = game_masters[0]
    assert initializer.prefab == "formative_memories_initializer__GameMaster"


def test_build_game_masters_raw_mode() -> None:
    cfg = _base_cfg("raw")
    game_masters = build_game_masters(cfg)

    initializer = game_masters[0]
    assert initializer.prefab == "raw_memories_initializer__GameMaster"
    assert initializer.params["module_path"] == "mastodon_sim.agents.initialization.raw"
