import concurrent.futures
import dataclasses
import os
from collections.abc import Mapping, Sequence
from typing import Any

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import game_master as gm_components
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib

# import importlib
from mastodon_sim import apps
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
        # apps = importlib.import_module(self.params["app_module_path"] + ".apps")
        sm_app = apps.SocialNetworkApp(
            action_logger=action_logger,
            perform_operations=cfg.social_media.use_server,  # self.params.get("use_server", False),
            app_description=self.params.get("app_description", ""),
        )

        user_mapping = {
            f"{agent_name.split()[0]}{agent_name.split()[1]}": f"user{i + 1:04d}"
            for i, agent_name in enumerate(user_data["sim_roles"])
        }  # first name keys
        sm_app.set_user_mapping(user_mapping)

        activity_transition_rates = self.set_app_state(sm_app, user_data)

        player_names = [entity.name for entity in self.entities]
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

    def set_agent_user_data(
        self,
        sm_app,
        agent,
        following_list,
    ) -> None:
        agent_name = agent._agent_name

        # initial list of users the agent is following
        for followee in following_list:
            sm_app.follow_user(agent_name, followee)

        # initial bio
        sm_app.update_profile(agent_name, bio="")

        # user's first post
        if hasattr(agent, "seed_post"):
            sm_app.post_toot(agent_name, status=agent.seed_post)
        else:
            sm_app.post_toot(agent_name, status=write_seed_toot(agent))

    def set_app_state(
        self,
        sm_app,
        user_data: dict[str, Any],
    ) -> dict[str, Any]:
        from sim.config_utils.social_media_functions import (
            generate_graph_from_networkx,
            generate_random_network,
        )

        activity_transition_rates: dict[str, Any] = {}
        # Determine network type from users config or default to 'random'
        network_type = user_data.get("network_type", "random")

        # Identify agents and candidates
        all_agents = list(user_data["sim_roles"].keys())
        candidates = [
            agent for agent, role in user_data["sim_roles"].items() if role == "candidate"
        ]

        if network_type == "random":
            following_lists = generate_random_network(
                all_agents, user_data, ensure_candidate_following=True
            )
        elif network_type == "barabasi_albert":
            # Default m=2
            following_lists = generate_graph_from_networkx(
                all_agents, candidates, graph_type="barabasi_albert", m=10
            )
        elif network_type == "lfr_benchmark":
            following_lists = generate_graph_from_networkx(
                all_agents, candidates, graph_type="lfr_benchmark"
            )
        elif network_type == "predefined":
            # Load from predefined list if available in user_data
            following_lists = user_data.get("predefined_graph", {})
            # Ensure defaults for agents not in predefined graph
            for agent in all_agents:
                if agent not in following_lists:
                    following_lists[agent] = []
        else:
            print(f"Unknown or generic network type '{network_type}', using random as fallback.")
            following_lists = generate_random_network(
                all_agents, user_data, ensure_candidate_following=True
            )

        for agent_i, role_i in user_data["sim_roles"].items():
            # per-step rate at which user is active
            activity_transition_rates[agent_i] = user_data["sim_role_parameters"][
                "activity_transition_rates"
            ][role_i]

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = []
            for agent in self.entities:
                futures.append(
                    executor.submit(
                        self.set_agent_user_data,
                        sm_app,
                        agent,
                        following_lists[agent._agent_name],
                    )
                )
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"Ignoring error during sm user data setting: {e}")

        return activity_transition_rates
