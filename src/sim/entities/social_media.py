import concurrent.futures
import dataclasses
import importlib
import os
import random
from collections.abc import Mapping, Sequence
from typing import Any

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import game_master as gm_components
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib

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
            "call_to_action", {"social media action": "Take an action on social media"}
        )
        user_data = self.params["sm_user_data"]
        call_to_sm_action = calls_to_action["social media action"]

        cfg = ConfigStore.get_config()
        action_logger = EventLogger(
            "action",
            os.path.join(cfg.sc.sim.output_rootname, f"{name.split('_')[0]}_action_events.jsonl"),
        )
        action_logger.episode_idx = 0
        apps = importlib.import_module(self.params["app_module_path"] + ".apps")
        sm_app = apps.SocialNetworkApp(
            action_logger=action_logger,
            perform_operations=self.params.get("use_server", False),
            app_description=self.params.get("app_description", ""),
        )

        user_mapping = {
            agent_name.split()[0]: f"user{i + 1:04d}"
            for i, agent_name in enumerate(user_data["sim_roles"])
        }  # first name keys
        print(user_mapping)
        sm_app.set_user_mapping(user_mapping)

        active_rates = self.set_app_state(sm_app, user_data)

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
            active_rates=active_rates,
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
        # initiailize initial followership network randomly based on pair role follow probabilities
        role_prob_matrix = user_data["sim_role_parameters"]["initial_follow_prob"]

        active_rates: dict[str, float] = {}
        following_lists: dict[str, list] = {}
        for agent_i, role_i in user_data["sim_roles"].items():
            # per-step rate at which user is active
            active_rates[agent_i] = user_data["sim_role_parameters"]["active_rates_per_episode"][
                role_i
            ]

            following_lists[agent_i] = []
            for agent_j, role_j in user_data["sim_roles"].items():
                if agent_i == agent_j:  # Agents cannot follow themselves
                    continue
                prob = role_prob_matrix[role_i][role_j]
                if random.random() < prob:
                    following_lists[agent_i].append(agent_j)

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

        return active_rates
