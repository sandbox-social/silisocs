"""Base social-media game master prefabs.

This module centralizes shared build logic used by both the simple single-flow
GM and the shared-flow GM variant.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import game_master as gm_components  # type: ignore[attr-defined]
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib
from omegaconf import OmegaConf

from mastodon_sim.environments.backends.factory import create_social_media_app
from mastodon_sim.environments.gm import act as gm_social_act
from mastodon_sim.environments.gm.components.factory import (
    build_backend_initializer,
    build_next_acting_component,
    build_observe_component,
    build_recommendation_component,
    build_resolve_component,
    initialize_component_flow_fields,
)
from mastodon_sim.environments.gm.components.seed_post_provider import (
    CSVSeedPostProvider,
    DisabledSeedPostProvider,
    FallbackSeedPostProvider,
    LLMSeedPostProvider,
    SeedPostProvider,
)
from mastodon_sim.runtime.action_prompts import (
    PromptAdditions,
    compile_action_prompt,
    prompt_additions_from_cfg,
)
from mastodon_sim.runtime.config import ConfigStore
from mastodon_sim.utils.misc import EventLogger

_LOGGER = logging.getLogger(__name__)


def _env_cfg(cfg: Any) -> Any:
    return getattr(cfg, "env", getattr(cfg, "environment", object()))


def _collect_seed_posts(
    entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
    provider: SeedPostProvider | None = None,
) -> dict[str, str]:
    """Collect seed posts using a configurable provider strategy.

    Args:
        entities: List of agent entities.
        provider: SeedPostProvider instance. Defaults to LLMSeedPostProvider if None.

    Returns
    -------
        Dict mapping agent name -> seed post text.
    """
    if provider is None:
        provider = LLMSeedPostProvider()
    return provider.get_seed_posts(entities)


def _compute_activity_rates(
    user_data: dict[str, Any],
) -> dict[str, dict[str, float]]:
    """Map each agent to its role's activity transition rates."""
    rates: dict[str, dict[str, float]] = {}
    transition_rates = user_data["sim_role_parameters"]["activity_transition_rates"]
    for agent, role in user_data["sim_roles"].items():
        rates[agent] = transition_rates[role]
    return rates


def _build_seed_post_provider(seed_post_cfg: dict[str, Any] | None = None) -> SeedPostProvider:
    """Build a seed post provider from configuration.

    Config format (optional):
        seed_posts:
          type: "llm"  # Options: "llm", "csv", "json", "fallback", "none"
          params:
            file_path: "/path/to/agents.csv"  # For CSV/JSON
            max_workers: 64  # For LLM (optional)
            llm_fallback: true  # For fallback mode

    Args:
        seed_post_cfg: Configuration dict for seed post provider.

    Returns
    -------
        Initialized SeedPostProvider instance (defaults to LLMSeedPostProvider).
    """
    _LOGGER.info(f"Building seed post provider with config: {seed_post_cfg}")
    if not seed_post_cfg:
        _LOGGER.info("No seed_post_cfg provided, using LLMSeedPostProvider")
        return LLMSeedPostProvider()

    provider_type = seed_post_cfg.get("type", "llm").lower()
    params = dict(seed_post_cfg.get("params", {}))

    _LOGGER.info(f"Seed post provider type: {provider_type}, params: {params}")

    if provider_type == "none":
        _LOGGER.info("No seed posts configured (organic growth)")
        return DisabledSeedPostProvider()

    if provider_type in ("csv", "json"):
        file_path = params.get("file_path")
        if not file_path:
            _LOGGER.warning(
                f"{provider_type.upper()} seed post provider requires 'file_path' parameter. Using LLM instead."
            )
            return LLMSeedPostProvider()
        _LOGGER.info(f"Creating CSVSeedPostProvider with path: {file_path}")
        return CSVSeedPostProvider(file_path)

    if provider_type == "fallback":
        file_path = params.get("file_path")
        llm_fallback = params.get("llm_fallback", True)
        _LOGGER.info(
            f"Creating FallbackSeedPostProvider with file_path: {file_path}, llm_fallback: {llm_fallback}"
        )
        return FallbackSeedPostProvider(file_path=file_path, llm_fallback=llm_fallback)

    # Default to "llm"
    max_workers = params.get("max_workers", 64)
    _LOGGER.info(f"Creating LLMSeedPostProvider with max_workers: {max_workers}")
    return LLMSeedPostProvider(max_workers=max_workers)


@dataclasses.dataclass
class BaseSocialMediaGameMaster(prefab_lib.Prefab):
    """Base social-media GM with configurable component slots."""

    description: str = "A social-media game master."
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "social-media_game-master",
            "calls_to_action": {},
            "app_module_path": "",
            "sim_role": {},
            "sm_user_data": {},
            "app_description": "",
        }
    )
    entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging] = ()

    def _is_shared_flow_mode(self) -> bool:
        return False

    def build_generic_prompt(
        self,
        *,
        cfg: Any,
        sm_app: Any,
        tool_calling_mode: str,
        gm_prompt_cfg: Mapping[str, Any] | None = None,
    ) -> str:
        """Build generic action prompt from backend action catalog for this GM.

        Generic prompts are generated at GM runtime so they reflect the current backend
        instance and enabled action set.
        """
        gm_prompt_cfg = dict(gm_prompt_cfg or {})
        output_style = str(gm_prompt_cfg.get("output_style", "") or "").strip()
        if not output_style:
            output_style = str(getattr(_env_cfg(cfg), "output_style", "") or "")

        additions = prompt_additions_from_cfg(cfg)
        base_prompt = str(sm_app.generate_generic_action_prompt() or "").strip()
        return compile_action_prompt(
            base_prompt=base_prompt,
            output_style=output_style,
            tool_calling_mode=tool_calling_mode,
            additions=PromptAdditions(
                add_action_count_guidance=additions.add_action_count_guidance,
            ),
        )

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        """Build and return the configured social-media game master entity."""
        del memory_bank
        name = str(self.params.get("name"))
        calls_to_action = self.params.get("calls_to_action", {})
        user_data = self.params["sm_user_data"]
        call_to_sm_action = calls_to_action.get("social_media_action", "")

        cfg = ConfigStore.get_config()
        action_logger = EventLogger(
            "action",
            os.path.join(cfg.sim.output_rootname, "action_events.jsonl"),
        )
        action_logger.episode_idx = 0

        platform_type = getattr(_env_cfg(cfg), "platform_type", "twitter_like")
        print(f"[DEBUG] Using platform_type: {platform_type}")
        db_path = os.path.join(cfg.sim.output_rootname, f"{platform_type}.db")
        sm_app = create_social_media_app(
            platform_type=platform_type,
            action_logger=action_logger,
            perform_operations=getattr(_env_cfg(cfg), "use_server", False),
            app_description=self.params.get("app_description", ""),
            db_path=db_path,
        )

        enabled_actions_cfg = getattr(_env_cfg(cfg), "enabled_actions", None)
        if enabled_actions_cfg is None:
            enabled_actions_cfg = getattr(cfg.sim, "enabled_actions", None)
        if enabled_actions_cfg is not None:
            if isinstance(enabled_actions_cfg, Sequence) and not isinstance(
                enabled_actions_cfg, (str, bytes)
            ):
                enabled_actions = [str(action).strip() for action in enabled_actions_cfg]
            else:
                enabled_actions = [str(enabled_actions_cfg).strip()]

            action_loop_built_in = ""
            if hasattr(cfg.simulator, "engine") and getattr(
                cfg.simulator.engine, "action_loop", None
            ):
                action_loop_built_in = str(
                    getattr(cfg.simulator.engine.action_loop, "built_in", "")
                ).strip()
            enabled_actions_upper = {name.upper() for name in enabled_actions if name}
            if action_loop_built_in == "open_ended" and "FINISHED" not in enabled_actions_upper:
                enabled_actions.append("FINISHED")

            sm_app.set_enabled_actions(enabled_actions)

        agent_names = [e.name for e in self.entities]

        # Build and apply seed post provider
        seed_post_cfg = {}

        # Seed posts come from composed scenario config when configured.
        env_cfg = _env_cfg(cfg)
        if hasattr(env_cfg, "seed_posts"):
            seed_post_cfg = cast(
                dict[str, Any],
                OmegaConf.to_container(env_cfg.seed_posts, resolve=True),
            )
        else:
            _LOGGER.info("No env.seed_posts found in composed config; proceeding without it.")

        seed_post_provider = _build_seed_post_provider(seed_post_cfg)
        _LOGGER.info(f"Using seed post provider: {type(seed_post_provider).__name__}")

        seed_t0 = time.time()
        seed_posts = _collect_seed_posts(self.entities, provider=seed_post_provider)
        seed_elapsed = time.time() - seed_t0

        activity_rates = _compute_activity_rates(user_data)
        entity_flow_tags = dict(user_data.get("entity_flow_tags", {}) or {})
        gm_orchestration = dict(user_data.get("gm_orchestration", {}) or {})
        gm_prompt_cfg = dict(gm_orchestration.get("prompt", {}) or {})

        catalog = sm_app.action_catalog()
        allowed_action_types = sorted(
            {
                str(item.get("selectable_name", "")).strip().upper()
                for item in catalog
                if str(item.get("selectable_name", "")).strip()
            }
        )
        for entity in self.entities:
            setter = getattr(entity, "set_allowed_action_types", None)
            existing = getattr(entity, "_allowed_action_types", None)
            if callable(setter) and not existing:
                setter(allowed_action_types)

        social_network_cfg = (
            dict(env_cfg.social_network) if hasattr(env_cfg, "social_network") else {}
        )

        gm_components_cfg: dict[str, Any] = {}
        env_gm_cfg = getattr(_env_cfg(cfg), "gm", None)
        if env_gm_cfg is not None and getattr(env_gm_cfg, "components", None) is not None:
            gm_components_cfg = cast(
                dict[str, Any],
                OmegaConf.to_container(
                    env_gm_cfg.components,
                    resolve=True,
                ),
            )
        elif hasattr(cfg.env, "gm") and getattr(cfg.env.gm, "components", None) is not None:
            gm_components_cfg = cast(
                dict[str, Any],
                OmegaConf.to_container(
                    cfg.env.gm.components,
                    resolve=True,
                ),
            )

        backend_initializer = build_backend_initializer(gm_components_cfg.get("initializer"))

        init_t0 = time.time()
        backend_initializer.initialize(
            sm_app=sm_app,
            agent_names=agent_names,
            init_kwargs={
                "sim_roles": user_data.get("sim_roles", {}),
                "seed_posts": seed_posts,
                "social_network": social_network_cfg,
            },
        )
        init_elapsed = time.time() - init_t0

        startup_line = (
            f"Startup social_init: seed_posts={seed_elapsed:.2f}s "
            f"seed_provider={type(seed_post_provider).__name__} "
            f"app_initialize={init_elapsed:.2f}s "
            f"initializer={type(backend_initializer).__name__} "
            f"agents={len(agent_names)} seed_count={sum(1 for t in seed_posts.values() if t)}"
        )
        _LOGGER.info(startup_line)
        stats_path = os.path.join(cfg.sim.output_rootname, "run_stats.log")
        with open(stats_path, "a", encoding="utf-8") as f:
            f.write(startup_line + "\n")

        player_names = agent_names
        # Map action_mode to default resolve component
        # Note: tool_calling is NOT an action_mode, only a resolve component option
        action_mode_to_resolve_map = {
            "custom": "parsed_action",
            "generic": "generic_action",
        }
        resolve_slot = dict(gm_components_cfg.get("resolve", {}))
        if not resolve_slot:
            # Use default resolver based on action_mode
            resolve_slot = {
                "built_in": action_mode_to_resolve_map.get(
                    getattr(cfg.sim, "action_mode", "custom"), "parsed_action"
                ),
            }

        # Determine if tool-calling is enabled from explicit mode config.
        tool_calling_mode = (
            str(OmegaConf.select(cfg, "simulator.tool_calling.mode", default="none") or "none")
            .strip()
            .lower()
        )
        action_mode = (
            str(getattr(cfg.simulator, "action_mode", "custom") or "custom").strip().lower()
        )
        if action_mode == "generic":
            call_to_sm_action = self.build_generic_prompt(
                cfg=cfg,
                sm_app=sm_app,
                tool_calling_mode=tool_calling_mode,
                gm_prompt_cfg=gm_prompt_cfg,
            )

        enable_tool_calling = tool_calling_mode in {"single", "multi"}
        action_output_mode = str(resolve_slot.get("built_in", "parsed_action") or "parsed_action")

        for entity in self.entities:
            mode_setter = getattr(entity, "set_action_output_mode", None)
            if callable(mode_setter):
                mode_setter(action_output_mode)

        next_actor = build_next_acting_component(
            gm_components_cfg.get("next_acting"),
            player_names=player_names,
            activity_transition_rates=activity_rates,
        )
        observe_slot = dict(gm_components_cfg.get("observe", {}))
        observe_params = dict(observe_slot.get("params") or {})
        episode_observation_flow = observe_params.get("episode_observation_flow", "fixed_pre")
        if isinstance(episode_observation_flow, list):
            episode_observation_flow = (
                episode_observation_flow[0] if episode_observation_flow else "fixed_pre"
            )

        timeline_mode = str(
            getattr(_env_cfg(cfg), "timeline_mode", None)
            or getattr(cfg.sim, "timeline_mode", "follower_chronological")
        )
        supported_timeline_modes = {
            "twitter_like": {
                "follower_chronological",
                "pure_recsys",
                "hybrid_recsys_follower",
                "curated_global",
            },
            "reddit_like": {
                "follower_chronological",
                "pure_recsys",
                "hybrid_recsys_follower",
            },
            "mastodon": {"follower_chronological"},
        }
        allowed_modes = supported_timeline_modes.get(platform_type, {"follower_chronological"})
        if timeline_mode not in allowed_modes:
            raise ValueError(
                f"Unsupported timeline_mode='{timeline_mode}' for platform '{platform_type}'. "
                f"Supported: {sorted(allowed_modes)}"
            )
        timeline_config = {}
        if hasattr(_env_cfg(cfg), "timeline_config"):
            timeline_config = (
                cast(
                    dict[str, Any],
                    OmegaConf.to_container(_env_cfg(cfg).timeline_config, resolve=True),
                )
                if isinstance(_env_cfg(cfg).timeline_config, dict)
                else {}
            )
        elif hasattr(cfg.env, "timeline_config"):
            timeline_config = (
                cast(
                    dict[str, Any],
                    OmegaConf.to_container(cfg.env.timeline_config, resolve=True),
                )
                if isinstance(cfg.env.timeline_config, dict)
                else {}
            )

        make_observation = build_observe_component(
            observe_slot,
            model=model,
            player_names=player_names,
            sm_app=sm_app,
            entity_flow_tags=entity_flow_tags,
            episode_observation_flow=str(episode_observation_flow),
            timeline_mode=timeline_mode,
            timeline_config=timeline_config,
        )
        resolve_component = build_resolve_component(
            resolve_slot,
            sm_app=sm_app,
            model=model,
            call_to_action_str=call_to_sm_action,
        )
        recommend_slot = dict(gm_components_cfg.get("recommend", {}))
        recommend_component = build_recommendation_component(
            recommend_slot,
            sm_app=sm_app,
            platform_type=platform_type,
            timeline_mode=timeline_mode,
        )

        # Initialize multi-field values if component supports them
        initialize_component_flow_fields(make_observation, observe_slot)
        initialize_component_flow_fields(resolve_component, resolve_slot)
        initialize_component_flow_fields(recommend_component, recommend_slot)
        if hasattr(recommend_component, "validate_recsys_types") and callable(
            recommend_component.validate_recsys_types
        ):
            recommend_component.validate_recsys_types()

        components = {
            gm_components.next_acting.DEFAULT_NEXT_ACTING_COMPONENT_KEY: next_actor,
            gm_components.make_observation.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY: make_observation,
            gm_components.event_resolution.DEFAULT_RESOLUTION_COMPONENT_KEY: resolve_component,
            "recommendation": recommend_component,
        }

        act_component = gm_social_act.SMAct(
            model=model,
            entity_names=player_names,
            component_order=list(components.keys()),
            call_to_action_str=call_to_sm_action,
            sm_app=sm_app,
            entity_flow_tags=entity_flow_tags,
            activity_transition_rates=activity_rates,
            action_mode=getattr(cfg.sim, "action_mode", "custom"),
            enable_tool_calling=enable_tool_calling,
        )

        # Stash orchestration metadata on the act component for engine-level schedulers.
        act_component.gm_orchestration = gm_orchestration
        act_component.shared_flow_mode = self._is_shared_flow_mode()

        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name,
            act_component=act_component,
            context_components=components,
        )
