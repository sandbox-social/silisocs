import dataclasses
from collections.abc import Mapping

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import agent as agent_components
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib

from .mastodon_action_suggester import (
    MastodonActionSuggester,
)


def _get_component_name(object_: object) -> str:
    if hasattr(object_, "name"):
        return object_.name
    return object_.__class__.__name__


def _get_class_name(object_: object) -> str:
    return object_.__class__.__name__


ACTION_PROBABILITIES = {
    # High frequency actions
    "like_toot": 0.35,  # Most common action
    "boost_toot": 0.15,  # Common but less than likes
    "toot": 0.20,  # Regular posting
    "reply": 0.15,
    # Medium frequency actions
    "follow": 0.10,  # Following new accounts
    "unfollow": 0.025,  # Unfollowing accounts
    "print_timeline": 0.0,  # Reading timeline
    # Low frequency actions
    "block_user": 0.0,  # Blocking problematic users
    "unblock_user": 0.0,  # Unblocking users
    "delete_posts": 0.0,  # Deleting own posts
    "update_bio": 0.0,  # Updating profile
    "print_notifications": 0.025,  # Checking notifications
}


class PublicOpinionCandidate(agent_components.question_of_recent_memories.QuestionOfRecentMemories):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class PublicOpinionOpponent(agent_components.question_of_recent_memories.QuestionOfRecentMemories):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


@dataclasses.dataclass
class Entity(prefab_lib.Prefab):
    """A prefab implementing an entity with a minimal set of components."""

    description: str = "An entity that simulates a malicious social media user who attempts to sway opinion towards a preferred candidate"
    params: Mapping[str, str] = dataclasses.field(
        default_factory=lambda: {
            "name": "",
            "goal": "",
            "election_info": "",
        }
    )


def build(
    self,
    model: language_model.LanguageModel,
    memory_bank: basic_associative_memory.AssociativeMemoryBank,
) -> entity_agent_with_logging.EntityAgentWithLogging:
    """Build an agent.

    Args:
      model: The language model to use.
      memory_bank: The agent's memory_bank object.

    Returns
    -------
      An entity.
    """
    agent_name = self.params.get("name", "Alice")
    election_info = self.params.get("election_info", "")
    randomize_choices = self.params.get("randomize_choices", True)
    goal = self.params.get("goal", "")

    memory_key = agent_components.memory.DEFAULT_MEMORY_COMPONENT_KEY
    memory = agent_components.memory.AssociativeMemory(memory_bank=memory_bank)

    instructions_key = "Instructions"
    instructions = agent_components.instructions.Instructions(
        agent_name=agent_name,
        pre_act_label="\nInstructions",
    )

    election_info_key = "Election Information"
    election_information = agent_components.constant.Constant(
        state=(election_info),
        pre_act_key="Critical election information\n",
    )

    observation_to_memory_key = "Observation"
    observation_to_memory = agent_components.observation.ObservationToMemory()

    observation_key = agent_components.observation.DEFAULT_OBSERVATION_COMPONENT_KEY
    observation = agent_components.observation.LastNObservations(
        history_length=50,
        pre_act_label=("\nEvents so far (ordered from least recent to most recent)"),
    )

    if goal:
        goal_label = "\nOverarching goal"
        overarching_goal = agent_components.constant.Constant(
            state=goal,
            pre_act_key=goal_label,
        )
    else:
        goal_label = None
        overarching_goal = None

    # identity_label = "\nIdentity characteristics"
    # identity_characteristics = (
    #     agent_components.question_of_query_associated_memories.IdentityWithoutPreAct(
    #         model=model,
    #         logging_channel=measurements.get_channel("IdentityWithoutPreAct").on_next,
    #         pre_act_key=identity_label,
    #     )
    # )
    # self_perception_label = f"\nQuestion: What kind of person is {agent_name}?\nAnswer"
    # self_perception = agent_components.question_of_recent_memories.SelfPerception(
    #     model=model,
    #     components={_get_class_name(identity_characteristics): identity_label},
    #     pre_act_key=self_perception_label,
    #     logging_channel=measurements.get_channel("SelfPerception").on_next,
    # )

    action_suggester_key = "Mastodon Action Suggestion"
    action_suggester = MastodonActionSuggester(
        model=model,
        action_probabilities=ACTION_PROBABILITIES,
    )
    components_of_agent = {
        # Components that provide pre_act context.
        instructions_key: instructions,
        election_info_key: election_information,
        observation_to_memory_key: observation_to_memory,
        observation_key: observation,
        # observation_summary,
        # relevant_memories,
        # self_perception,
        action_suggester_key: action_suggester,
        # Components that do not provide pre_act context.
        # identity_characteristics,
        memory_key: memory,
    }

    component_order = list(components_of_agent.keys())
    if overarching_goal is not None:
        if goal_label is not None:
            components_of_agent[goal_label] = overarching_goal
            component_order.insert(1, goal_label)

    act_component = agent_components.concat_act_component.ConcatActComponent(
        model=model,
        component_order=component_order,
        randomize_choices=randomize_choices,
    )

    agent = entity_agent_with_logging.EntityAgentWithLogging(
        agent_name=agent_name,
        act_component=act_component,
        context_components=components_of_agent,
    )

    return agent
