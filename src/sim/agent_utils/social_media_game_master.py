import dataclasses
import os
from collections.abc import Mapping, Sequence
from typing import Any

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import game_master as gm_components
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib

from mastodon_sim.concordia.components import apps
from sim.agent_utils.components import gm_social_act, social_make_observation
from sim.sim_utils.misc_sim_utils import EventLogger


@dataclasses.dataclass
class SocialMediaGM(prefab_lib.Prefab):
    """A prefab entity implementing a social media game master."""

    description: str = "A social-media game master."
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "mastodon-game-master",
            "call_to_action_str": "",
            "sm_app_data": {},
            "use_server": False,
            "app_description": "",
            "output_path": "",
            "active_rates": {},
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
        name = self.params.get("name")
        call_to_action_str = self.params.get("call_to_action_str", "")
        sm_app = self.params.get("sm_app")

        action_logger = EventLogger(
            "action", os.path.join(self.params.get("output_path", ""), "action_events.jsonl")
        )
        mastodon_app = apps.MastodonSocialNetworkApp(
            action_logger=action_logger,
            perform_operations=self.params.get("use_server", False),
            app_description=self.params.get("app_description", ""),
        )
        mastodon_app.set_user_mapping(self.params.get("sm_app_data", {}))
        # TODO: Replace with helper functions

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
            call_to_action_str=call_to_action_str,
            sm_app=mastodon_app,
        )

        game_master = entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name,
            act_component=act_component,
            context_components=components_of_game_master,
        )

        return game_master
