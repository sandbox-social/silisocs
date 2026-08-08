from typing import Any, cast

import pytest
from omegaconf import OmegaConf

pytest.importorskip("psutil")

from silisocs.runtime.construction.initialization_context import (
    build_initializer_context,
    populate_agent_data,
)
from silisocs.runtime.construction.specs import AgentConfig, GameMasterConfig
from silisocs.runtime.execution.session import build_game_masters, resolve_flow_chains


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
                "gm": {
                    "backend": {
                        "type": "twitter_like",
                        "class_path": None,
                        "params": {},
                        "enabled_actions": None,
                    },
                    "class_path": "silisocs.environments.gm.game_master.ComponentGameMaster",
                    "name": "social-media_game-master",
                    "components": {
                        "initialize": {
                            "built_in": "social_media",
                            "params": {
                                "graph": {
                                    "fully_connected_targets": [],
                                    "base_followership_probability": 0.3,
                                }
                            },
                        },
                        "next_acting": {"built_in": "all_agents"},
                        "observe": {
                            "built_in": "timeline_every_turn",
                            "params": {"timeline_mode": "follower_chronological"},
                        },
                        "resolve": {"built_in": "parsed_action"},
                        "action_prompt": {
                            "built_in": "default",
                            "params": {
                                "action_prompt": "Act on timeline.",
                                "output_style": "",
                            },
                        },
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

    with pytest.raises(ValueError, match="seed_posts"):
        build_game_masters(cfg)


def test_old_gm_initializer_config_is_rejected() -> None:
    cfg = _base_cfg("raw")
    cfg.env.gm.components.initializer = {"built_in": "backend_default"}

    with pytest.raises(ValueError, match="initializer"):
        build_game_masters(cfg)


def test_unknown_env_structure_key_is_rejected() -> None:
    cfg = _base_cfg("raw")
    cfg.env.extra = {"backend": {"type": "twitter_like"}}

    with pytest.raises(ValueError, match="extra"):
        build_game_masters(cfg)


def test_old_env_backend_config_is_rejected() -> None:
    cfg = _base_cfg("raw")
    cfg.env.backend = {"type": "twitter_like"}

    with pytest.raises(ValueError, match="backend"):
        build_game_masters(cfg)


def test_old_env_app_config_is_rejected() -> None:
    cfg = _base_cfg("raw")
    cfg.env.app = {"class_path": "tests.fake.App", "params": {}}

    with pytest.raises(ValueError, match="app"):
        build_game_masters(cfg)


def test_removed_local_llm_provider_is_rejected() -> None:
    cfg = _base_cfg("raw")
    cfg.sim.llm = {"provider": "local"}

    with pytest.raises(ValueError, match="Unsupported sim.llm.provider"):
        build_game_masters(cfg)


def test_build_game_masters_supports_per_gm_prompt_overrides() -> None:
    cfg = _base_cfg("raw")
    base_components = cast(
        dict[str, Any], OmegaConf.to_container(cfg.env.gm.components, resolve=True)
    )
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm_alpha",
                "sequence": 0,
                "backend": {
                    "type": "twitter_like",
                    "class_path": None,
                    "params": {},
                    "enabled_actions": None,
                },
                "components": {
                    **base_components,
                    "action_prompt": {
                        "built_in": "default",
                        "params": {
                            "action_prompt": "Alpha prompt body\n[OUTPUT STYLE]",
                            "output_style": "Alpha output style",
                        },
                    },
                },
            },
            {
                "gm_name": "gm_beta",
                "sequence": 1,
                "backend": {
                    "type": "twitter_like",
                    "class_path": None,
                    "params": {},
                    "enabled_actions": None,
                },
                "components": {
                    **base_components,
                    "action_prompt": {
                        "built_in": "default",
                        "params": {
                            "action_prompt": "Beta prompt body\n[OUTPUT STYLE]",
                            "output_style": "Beta output style",
                        },
                    },
                },
            },
        ],
        "flow_bindings": {
            "flow_to_gms": {},
        },
    }

    game_masters = build_game_masters(cfg)
    social_media_gms = list(game_masters)
    prompts_by_name = {
        gm.params["name"]: gm.params["action_prompt_template"] for gm in social_media_gms
    }

    assert "gm_alpha" in prompts_by_name
    assert "gm_beta" in prompts_by_name
    assert "Alpha prompt body" in prompts_by_name["gm_alpha"]
    assert "Alpha output style" in prompts_by_name["gm_alpha"]
    assert "Beta prompt body" in prompts_by_name["gm_beta"]
    assert "Beta output style" in prompts_by_name["gm_beta"]


def test_multi_gm_specs_require_own_backend_and_components() -> None:
    cfg = _base_cfg("raw")
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm_alpha",
                "sequence": 0,
                "components": cast(
                    dict[str, Any], OmegaConf.to_container(cfg.env.gm.components, resolve=True)
                ),
            }
        ]
    }

    with pytest.raises(ValueError, match="backend is required"):
        build_game_masters(cfg)

    cfg = _base_cfg("raw")
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm_alpha",
                "sequence": 0,
                "backend": OmegaConf.to_container(cfg.env.gm.backend, resolve=True),
            }
        ]
    }

    with pytest.raises(ValueError, match="components is required"):
        build_game_masters(cfg)


def test_multi_gm_specs_can_use_distinct_backends() -> None:
    cfg = _base_cfg("raw")
    social_components = cast(
        dict[str, Any], OmegaConf.to_container(cfg.env.gm.components, resolve=True)
    )
    social_components["resolve"] = {"built_in": "parsed_action", "params": {}}
    market_components = {
        "initialize": {"built_in": "app_initialize", "params": {}},
        "next_acting": {"built_in": "fixed_order", "params": {}},
        "observe": {"built_in": "app_observation", "params": {}},
        "resolve": {"built_in": "parsed_action", "params": {}},
        "update": {"built_in": "disabled", "params": {}},
        "action_prompt": {"built_in": "default", "params": {}},
    }
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "social_gm",
                "sequence": 0,
                "backend": {
                    "type": "twitter_like",
                    "class_path": None,
                    "params": {},
                    "enabled_actions": None,
                },
                "components": social_components,
            },
            {
                "gm_name": "market_gm",
                "sequence": 1,
                "backend": {
                    "type": "resource_market",
                    "class_path": None,
                    "params": {"initial_cash": 5},
                    "enabled_actions": None,
                },
                "components": market_components,
            },
        ],
        "flow_bindings": {"flow_to_gms": {"social": ["social_gm"], "market": ["market_gm"]}},
    }

    specs = build_game_masters(cfg)

    by_name = {spec.params["name"]: spec for spec in specs}
    assert by_name["social_gm"].params["backend_config"]["backend_type"] == "twitter_like"
    assert by_name["market_gm"].params["backend_config"]["backend_type"] == "resource_market"
    assert by_name["market_gm"].params["backend_config"]["params"] == {"initial_cash": 5}


def test_multi_gm_nested_backend_and_component_keys_are_strict() -> None:
    cfg = _base_cfg("raw")
    components = cast(dict[str, Any], OmegaConf.to_container(cfg.env.gm.components, resolve=True))
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm_alpha",
                "sequence": 0,
                "backend": {
                    "type": "twitter_like",
                    "class_path": None,
                    "params": {},
                    "legacy_app": {},
                },
                "components": components,
            }
        ]
    }

    with pytest.raises(ValueError, match="legacy_app"):
        build_game_masters(cfg)

    cfg = _base_cfg("raw")
    components = cast(dict[str, Any], OmegaConf.to_container(cfg.env.gm.components, resolve=True))
    components["legacy_slot"] = {"built_in": "none"}
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm_alpha",
                "sequence": 0,
                "backend": OmegaConf.to_container(cfg.env.gm.backend, resolve=True),
                "components": components,
            }
        ]
    }

    with pytest.raises(ValueError, match="legacy_slot"):
        build_game_masters(cfg)


def test_multi_gm_flow_bindings_validate_unknown_duplicate_and_sequence() -> None:
    cfg = _base_cfg("raw")
    components = cast(dict[str, Any], OmegaConf.to_container(cfg.env.gm.components, resolve=True))
    backend = OmegaConf.to_container(cfg.env.gm.backend, resolve=True)
    cfg.env.gm_orchestration = {
        "gms": [
            {"gm_name": "first", "sequence": 1, "backend": backend, "components": components},
            {"gm_name": "second", "sequence": 0, "backend": backend, "components": components},
        ],
        "flow_bindings": {"flow_to_gms": {"review": ["first", "missing"]}},
    }

    with pytest.raises(ValueError, match="Unknown GMs"):
        build_game_masters(cfg)

    cfg.env.gm_orchestration.flow_bindings.flow_to_gms.review = ["first", "first"]
    with pytest.raises(ValueError, match="duplicate GMs"):
        build_game_masters(cfg)

    cfg.env.gm_orchestration.flow_bindings.flow_to_gms.review = ["first", "second"]
    with pytest.raises(ValueError, match="strictly serial"):
        build_game_masters(cfg)


def test_multi_gm_flow_chain_empty_slots_are_preserved_and_validated() -> None:
    cfg = _base_cfg("raw")
    components = cast(dict[str, Any], OmegaConf.to_container(cfg.env.gm.components, resolve=True))
    backend = OmegaConf.to_container(cfg.env.gm.backend, resolve=True)
    cfg.env.gm_orchestration = {
        "gms": [
            {"gm_name": "first", "sequence": 1, "backend": backend, "components": components},
            {"gm_name": "second", "sequence": 0, "backend": backend, "components": components},
        ],
        # 'second'(seq 0), an empty slot, then 'first'(seq 1): the None is an idle
        # stage preserved in place; the real chain stays strictly serial by sequence.
        "flow_bindings": {"flow_to_gms": {"review": ["second", None, "first"]}},
    }

    # flow_chains is engine config (resolved via resolve_flow_chains), not GM params.
    assert resolve_flow_chains(cfg)["review"] == ["second", None, "first"]
    assert "flow_chains" not in build_game_masters(cfg)[0].params

    # Trailing empty slots have no effect under any traversal, so they are trimmed.
    cfg.env.gm_orchestration.flow_bindings.flow_to_gms.review = ["second", "first", None]
    assert resolve_flow_chains(cfg)["review"] == ["second", "first"]

    # A chain made up only of empty slots has no real GM and is rejected.
    cfg.env.gm_orchestration.flow_bindings.flow_to_gms.review = [None, None]
    with pytest.raises(ValueError, match="cannot be empty"):
        resolve_flow_chains(cfg)


def test_build_game_masters_includes_agent_to_flow_override_in_owned_flows() -> None:
    cfg = _base_cfg("raw")
    cfg.sim.engine = {
        "step": {"built_in": "multi_gm", "params": {"agent_to_flow": {"Alice": "override"}}},
        "turn_policy": {"built_in": "single_action"},
    }
    cfg.env.gm_orchestration = {
        "gms": [
            {
                "gm_name": "gm_alpha",
                "sequence": 0,
                "backend": OmegaConf.to_container(cfg.env.gm.backend, resolve=True),
                "components": OmegaConf.to_container(cfg.env.gm.components, resolve=True),
            }
        ],
        "flow_bindings": {"flow_to_gms": {}},
    }

    specs = build_game_masters(cfg)

    assert specs[0].params["owned_flows"] == ["override", "default"]


def test_populate_agent_data_updates_every_game_master_spec() -> None:
    cfg = OmegaConf.create(
        {"sim": {"engine": {"step": {"params": {"agent_to_flow": {"Bob": "override"}}}}}}
    )
    agents = [
        AgentConfig(
            class_path="silisocs.agents.native.NativeAgent",
            params={"name": "Alice", "sim_role": {"name": "speaker"}, "flow_tag": "review"},
        ),
        AgentConfig(
            class_path="silisocs.agents.native.NativeAgent",
            params={"name": "Bob", "sim_role": {"name": "listener"}},
        ),
    ]
    game_masters = [
        GameMasterConfig(class_path="tests.fake.GM", params={"name": "gm_one"}),
        GameMasterConfig(class_path="tests.fake.GM", params={"name": "gm_two"}),
    ]

    populate_agent_data(cfg, agents, game_masters)

    for gm in game_masters:
        assert gm.params["sim_roles"] == {"Alice": "speaker", "Bob": "listener"}
        assert gm.params["agent_flow_tags"] == {"Alice": "review", "Bob": "override"}

    context = build_initializer_context(cfg, agents)
    assert context.agent_flow_tags == {"Alice": "review", "Bob": "override"}


def test_agent_to_flow_rejects_unknown_agent_name() -> None:
    cfg = OmegaConf.create(
        {"sim": {"engine": {"step": {"params": {"agent_to_flow": {"Ghost": "review"}}}}}}
    )
    agents = [
        AgentConfig(
            class_path="silisocs.agents.native.NativeAgent",
            params={"name": "Alice", "sim_role": {"name": "speaker"}, "flow_tag": "default"},
        )
    ]
    game_masters = [GameMasterConfig(class_path="tests.fake.GM", params={"name": "gm"})]

    with pytest.raises(ValueError, match="unknown agent"):
        populate_agent_data(cfg, agents, game_masters)
