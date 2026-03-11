"""Social-media game master prefab.

Builds the runtime game master that drives agent actions each episode.
Network generation, seed post collection, and platform initialization are
delegated to the platform-specific ``SocialMediaApp.initialize()``.
"""

import dataclasses
import logging
import os
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import game_master as gm_components
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib

from mastodon_sim.environments.backends.factory import create_social_media_app
from mastodon_sim.environments.gm_components import act as gm_social_act
from mastodon_sim.evaluations.probes.agent_speech import write_seed_toot
from mastodon_sim.runtime.config import ConfigStore
from mastodon_sim.utils.misc import EventLogger

_LOGGER = logging.getLogger(__name__)


def _collect_seed_posts(
    entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
) -> dict[str, str]:
    """Collect seed posts from entities — use attribute if present, else LLM-generate."""
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
    """Social-media game master — thin orchestrator.

    Network generation, follower setup, and seed posts are handled entirely
    by the platform ``SocialMediaApp``.  The GM only collects seed posts
    (since that may require the LLM) and passes everything through.
    """

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

        agent_names = [e.name for e in self.entities]

        # Seed posts (may need LLM, so stays in the GM).
        seed_t0 = time.time()
        seed_posts = _collect_seed_posts(self.entities)
        seed_elapsed = time.time() - seed_t0

        # Activity transition rates.
        activity_rates = _compute_activity_rates(user_data)

        # Read social_network config and pass to the app.
        social_network_cfg = dict(cfg.scenario.social_network) if hasattr(cfg.scenario, "social_network") else {}

        init_t0 = time.time()
        sm_app.initialize(
            agent_names=agent_names,
            sim_roles=user_data.get("sim_roles", {}),
            seed_posts=seed_posts,
            social_network=social_network_cfg,
        )
        init_elapsed = time.time() - init_t0

        startup_line = (
            f"Startup social_init: seed_posts={seed_elapsed:.2f}s "
            f"app_initialize={init_elapsed:.2f}s "
            f"agents={len(agent_names)} seed_count={sum(1 for t in seed_posts.values() if t)}"
        )
        _LOGGER.info(startup_line)
        stats_path = os.path.join(cfg.sim.output_rootname, "run_stats.log")
        with open(stats_path, "a", encoding="utf-8") as f:
            f.write(startup_line + "\n")

        # Wire GM components.
        player_names = agent_names
        next_actor = gm_components.next_acting.NextActingAllEntities(player_names=player_names)
        components = {gm_components.next_acting.DEFAULT_NEXT_ACTING_COMPONENT_KEY: next_actor}

        act_component = gm_social_act.SMAct(
            model=model,
            entity_names=player_names,
            component_order=list(components.keys()),
            call_to_action_str=call_to_sm_action,
            sm_app=sm_app,
            activity_transition_rates=activity_rates,
            action_mode=getattr(cfg.sim, "action_mode", "custom"),
        )

        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name,
            act_component=act_component,
            context_components=components,
        )
