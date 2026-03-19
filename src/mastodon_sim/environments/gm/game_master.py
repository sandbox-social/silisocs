"""Social-media game master prefab.

Builds the runtime game master that drives agent actions each episode.
Network generation, seed post collection, and platform initialization are
delegated to configurable backend and GM components.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    build_resolve_component,
)
from mastodon_sim.evaluations.probes.agent_speech import write_seed_toot
from mastodon_sim.runtime.config import ConfigStore
from mastodon_sim.utils.misc import EventLogger

_LOGGER = logging.getLogger(__name__)


def _collect_seed_posts(
    entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
) -> dict[str, str]:
    """Collect seed posts from entities, generating missing ones with LLM calls."""
    seed_posts: dict[str, str] = {}
    llm_seed_agents = []
    for agent in entities:
        name = agent._agent_name
        if hasattr(agent, "seed_post") and agent.seed_post:
            seed_posts[name] = agent.seed_post
        else:
            llm_seed_agents.append(agent)

    if llm_seed_agents:
        workers = min(len(llm_seed_agents), 64)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_name = {
                executor.submit(write_seed_toot, a): a._agent_name for a in llm_seed_agents
            }
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    seed_posts[name] = future.result()
                except Exception:
                    _LOGGER.exception("Seed post generation failed for %s", name)
                    seed_posts[name] = ""
    return seed_posts


def _compute_activity_rates(
    user_data: dict[str, Any],
) -> dict[str, dict[str, float]]:
    """Map each agent to its role's activity transition rates."""
    rates: dict[str, dict[str, float]] = {}
    transition_rates = user_data["sim_role_parameters"]["activity_transition_rates"]
    for agent, role in user_data["sim_roles"].items():
        rates[agent] = transition_rates[role]
    return rates


@dataclasses.dataclass
class GameMaster(prefab_lib.Prefab):
    """Social-media game master with YAML-selectable Concordia components."""

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

        platform_type = getattr(cfg.social_media, "platform_type", "twitter_like")
        db_path = os.path.join(cfg.sim.output_rootname, f"{platform_type}.db")
        sm_app = create_social_media_app(
            platform_type=platform_type,
            action_logger=action_logger,
            perform_operations=getattr(cfg.social_media, "use_server", False),
            app_description=self.params.get("app_description", ""),
            db_path=db_path,
        )

        enabled_actions_cfg = getattr(cfg.sim, "enabled_actions", None)
        if enabled_actions_cfg is not None:
            if isinstance(enabled_actions_cfg, (list, tuple)):
                enabled_actions = [str(name).strip() for name in enabled_actions_cfg]
            else:
                enabled_actions = [str(enabled_actions_cfg).strip()]
            sm_app.set_enabled_actions(enabled_actions)

        agent_names = [e.name for e in self.entities]

        seed_t0 = time.time()
        seed_posts = _collect_seed_posts(self.entities)
        seed_elapsed = time.time() - seed_t0

        activity_rates = _compute_activity_rates(user_data)
        entity_action_flows = dict(user_data.get("entity_action_flows", {}) or {})

        # Optional one-time initialization hook for specialized entities.
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
            dict(cfg.scenario.social_network) if hasattr(cfg.scenario, "social_network") else {}
        )

        gm_components_cfg: dict[str, Any] = {}
        if hasattr(cfg.sim, "gm") and getattr(cfg.sim.gm, "components", None) is not None:
            gm_components_cfg = cast(
                dict[str, Any],
                OmegaConf.to_container(
                    cfg.sim.gm.components,
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
            f"app_initialize={init_elapsed:.2f}s "
            f"initializer={type(backend_initializer).__name__} "
            f"agents={len(agent_names)} seed_count={sum(1 for t in seed_posts.values() if t)}"
        )
        _LOGGER.info(startup_line)
        stats_path = os.path.join(cfg.sim.output_rootname, "run_stats.log")
        with open(stats_path, "a", encoding="utf-8") as f:
            f.write(startup_line + "\n")

        player_names = agent_names
        resolve_mode_map = {
            "custom": "parsed_action",
            "generic": "generic_action",
            "tool_calling": "tool_calling",
        }
        resolve_slot = dict(gm_components_cfg.get("resolve", {}))
        if not resolve_slot:
            resolve_slot = {
                "built_in": resolve_mode_map.get(getattr(cfg.sim, "action_mode", "custom")),
            }
        else:
            slot_built_in = resolve_slot.get("built_in")
            slot_class = resolve_slot.get("class_path")
            slot_params = resolve_slot.get("params")
            action_mode = getattr(cfg.sim, "action_mode", "custom")
            if (
                slot_built_in == "parsed_action"
                and not slot_class
                and not slot_params
                and action_mode in {"generic", "tool_calling"}
            ):
                resolve_slot["built_in"] = resolve_mode_map[action_mode]

        enable_tool_calling = (
            resolve_slot.get("built_in") == "tool_calling"
            or getattr(cfg.sim, "action_mode", "custom") == "tool_calling"
        )

        next_actor = build_next_acting_component(
            gm_components_cfg.get("next_acting"),
            player_names=player_names,
            activity_transition_rates=activity_rates,
        )
        observe_slot = dict(gm_components_cfg.get("observe", {}))
        observe_params = dict(observe_slot.get("params") or {})
        episode_observation_flows = observe_params.get("episode_observation_flows")
        if episode_observation_flows is None:
            episode_observation_flows = ["fixed_pre"]
        elif isinstance(episode_observation_flows, str):
            episode_observation_flows = [episode_observation_flows]

        make_observation = build_observe_component(
            observe_slot,
            model=model,
            player_names=player_names,
            sm_app=sm_app,
            entity_action_flows=entity_action_flows,
            episode_observation_flows=list(episode_observation_flows),
        )
        resolve_component = build_resolve_component(
            resolve_slot,
            sm_app=sm_app,
            model=model,
            call_to_action_str=call_to_sm_action,
        )

        components = {
            gm_components.next_acting.DEFAULT_NEXT_ACTING_COMPONENT_KEY: next_actor,
            gm_components.make_observation.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY: make_observation,
            gm_components.event_resolution.DEFAULT_RESOLUTION_COMPONENT_KEY: resolve_component,
        }

        act_component = gm_social_act.SMAct(
            model=model,
            entity_names=player_names,
            component_order=list(components.keys()),
            call_to_action_str=call_to_sm_action,
            sm_app=sm_app,
            entity_action_flows=entity_action_flows,
            activity_transition_rates=activity_rates,
            action_mode=getattr(cfg.sim, "action_mode", "custom"),
            enable_tool_calling=enable_tool_calling,
        )

        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name,
            act_component=act_component,
            context_components=components,
        )
