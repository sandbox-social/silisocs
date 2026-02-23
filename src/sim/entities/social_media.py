import dataclasses
import os
from collections.abc import Mapping, Sequence
from typing import Any

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import game_master as gm_components
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib

from sim.core.app_factory import create_social_media_app
from sim.entities.components import gm_social_act, social_make_observation
from sim.sim_utils.agent_speech_utils import (
    write_seed_toot,
)
from sim.sim_utils.misc_sim_utils import ConfigStore, EventLogger


@dataclasses.dataclass
class GameMaster(prefab_lib.Prefab):
    """A prefab entity implementing a social media game master."""

    description: str = "A social-media game master."
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "social-media_game-master",
            "calls_to_action": {},
            "app_module_path": "",
            "sim_role": {},
            "sm_user_data": {},
            "use_server": False,
            "app_description": "",
        }
    )
    entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging] = ()

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        """Build the game master.

        Args:
          model: The language model to use.
          memory_bank: The memory bank to use.

        Returns
        -------
          An entity.
        """
        name = str(self.params.get("name"))
        calls_to_action = self.params.get(
            "calls_to_action", {"social media action": "Take an action on social media"}
        )
        user_data = self.params["sm_user_data"]
        call_to_sm_action = calls_to_action["social_media_action"]

        cfg = ConfigStore.get_config()
        action_logger = EventLogger(
            "action",
            os.path.join(cfg.sim.output_rootname, "action_events.jsonl"),
        )
        action_logger.episode_idx = 0

        # Use the factory to create the correct platform app
        platform_type = getattr(cfg.social_media, "platform_type", "mastodon")
        # Store platform DB in the sim output directory
        db_path = os.path.join(cfg.sim.output_rootname, f"{platform_type}.db")
        sm_app = create_social_media_app(
            platform_type=platform_type,
            action_logger=action_logger,
            perform_operations=getattr(cfg.social_media, "use_server", False),
            app_description=self.params.get("app_description", ""),
            db_path=db_path,
        )

        # Build network and collect initialization data
        following_lists, agent_bios, seed_posts, activity_transition_rates = (
            self._prepare_app_state(sm_app, user_data)
        )

        # Delegate all initialization to the platform app
        agent_names = [entity.name for entity in self.entities]
        sm_app.initialize(
            agent_names=agent_names,
            sim_roles=user_data.get("sim_roles", {}),
            following_network=following_lists,
            agent_bios=agent_bios,
            seed_posts=seed_posts,
        )

        player_names = agent_names
        make_observation_key = social_make_observation.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY
        make_observation = social_make_observation.SimpleMakeObservation(
            model=model,
            player_names=player_names,
        )
        next_actor_key = gm_components.next_acting.DEFAULT_NEXT_ACTING_COMPONENT_KEY
        next_actor = gm_components.next_acting.NextActingAllEntities(
            player_names=player_names,
        )
        components_of_game_master = {
            make_observation_key: make_observation,
            next_actor_key: next_actor,
        }

        component_order = list(components_of_game_master.keys())

        act_component = gm_social_act.SMAct(
            model=model,
            entity_names=player_names,
            component_order=component_order,
            call_to_action_str=call_to_sm_action,
            sm_app=sm_app,
            activity_transition_rates=activity_transition_rates,
        )

        game_master = entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name,
            act_component=act_component,
            context_components=components_of_game_master,
        )

        return game_master

    def _prepare_app_state(
        self,
        sm_app,
        user_data: dict[str, Any],
    ) -> tuple[dict, dict, dict, dict]:
        """Generate social network, collect bios/seed posts, compute activity rates.

        Returns
        -------
            A tuple of (following_lists, agent_bios, seed_posts,
            activity_transition_rates).
        """
        from sim.config_utils.social_media_functions import (
            generate_graph_from_networkx,
            generate_random_network,
        )

        # --- Network generation ---
        network_type = user_data.get("network_type", "random")
        all_agents = list(user_data["sim_roles"].keys())
        candidates = [
            agent for agent, role in user_data["sim_roles"].items() if role == "candidate"
        ]

        if network_type == "random":
            following_lists = generate_random_network(
                all_agents, user_data, ensure_candidate_following=True
            )
        elif network_type == "barabasi_albert":
            following_lists = generate_graph_from_networkx(
                all_agents, candidates, graph_type="barabasi_albert", m=10
            )
        elif network_type == "lfr_benchmark":
            following_lists = generate_graph_from_networkx(
                all_agents, candidates, graph_type="lfr_benchmark"
            )
        elif network_type == "predefined":
            following_lists = user_data.get("predefined_graph", {})
            for agent in all_agents:
                if agent not in following_lists:
                    following_lists[agent] = []
        else:
            print(f"Unknown network type '{network_type}', using random as fallback.")
            following_lists = generate_random_network(
                all_agents, user_data, ensure_candidate_following=True
            )

        # --- Agent bios and seed posts ---
        agent_bios: dict[str, str] = {}
        seed_posts: dict[str, str] = {}
        for agent in self.entities:
            agent_name = agent._agent_name
            agent_bios[agent_name] = ""
            if hasattr(agent, "seed_post"):
                seed_posts[agent_name] = agent.seed_post
            else:
                seed_posts[agent_name] = write_seed_toot(agent)

        # --- Activity transition rates ---
        activity_transition_rates: dict[str, Any] = {}
        for agent_i, role_i in user_data["sim_roles"].items():
            activity_transition_rates[agent_i] = user_data["sim_role_parameters"][
                "activity_transition_rates"
            ][role_i]

        return following_lists, agent_bios, seed_posts, activity_transition_rates
