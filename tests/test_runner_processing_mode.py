import pytest
from omegaconf import OmegaConf

pytest.importorskip("psutil")

from silisocs.runtime.execution.session import build_game_masters


def _base_cfg(processing_mode: str):
    return OmegaConf.create(
        {
            "sim": {
                "action_mode": "custom",
                "tool_calling": {"mode": "none"},
                "prompt_additions": {"action_count_guidance": True},
                "initialization": {
                    "agents": {"built_in": "default", "class_path": None, "params": {}},
                    "game_masters": {"built_in": "default", "class_path": None, "params": {}},
                    "simulation": {"built_in": "none", "class_path": None, "params": {}},
                },
            },
            "agents": {
                "shared_memories": ["shared memory"],
                "persona_pipeline": {},
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


def test_build_runtime_initializer_supports_raw_memory() -> None:
    from silisocs.initialization.agents import RawMemoryAgentInitializer, build_agent_initializer

    initializer = build_agent_initializer({"built_in": "raw_memory"})

    assert isinstance(initializer, RawMemoryAgentInitializer)


def test_build_runtime_initializer_supports_formative_memory() -> None:
    from silisocs.initialization.agents import FormativeAgentInitializer, build_agent_initializer

    initializer = build_agent_initializer({"built_in": "formative_memory"})

    assert isinstance(initializer, FormativeAgentInitializer)


def test_runtime_initializer_rejects_old_top_level_memory_alias() -> None:
    from silisocs.initialization.agents import build_agent_initializer

    with pytest.raises(ValueError, match="Unknown sim.initialization.agents"):
        build_agent_initializer({"built_in": "raw"})


def test_build_game_masters_returns_only_environment_gms() -> None:
    cfg = _base_cfg("raw")
    game_masters = build_game_masters(cfg)

    assert len(game_masters) == 1
    assert [gm.role.value for gm in game_masters] == ["game_master"]


def test_processing_mode_is_rejected() -> None:
    cfg = _base_cfg("raw")
    cfg.agents.persona_pipeline.processing_mode = "raw"

    with pytest.raises(ValueError, match="processing_mode"):
        build_game_masters(cfg)


def test_old_env_seed_posts_config_is_rejected() -> None:
    cfg = _base_cfg("raw")
    cfg.env.seed_posts = {"type": "none"}

    with pytest.raises(ValueError, match="env.seed_posts"):
        build_game_masters(cfg)


def test_old_gm_initializer_config_is_rejected() -> None:
    cfg = _base_cfg("raw")
    cfg.env.gm.components.initializer = {"built_in": "backend_default"}

    with pytest.raises(ValueError, match="env.gm.components.initializer"):
        build_game_masters(cfg)


def test_build_game_masters_supports_per_gm_prompt_overrides() -> None:
    cfg = _base_cfg("raw")
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm_alpha",
                "sequence": 0,
                "initializer": {"built_in": "social_media", "class_path": None, "params": {}},
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
                "initializer": {"built_in": "social_media", "class_path": None, "params": {}},
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
    social_media_gms = list(game_masters)
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
