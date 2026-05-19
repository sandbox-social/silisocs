import dataclasses
from collections.abc import Mapping

from concordia.agents.entity_agent_with_logging import EntityAgentWithLogging as ComponentEntity
from concordia.associative_memory.basic_associative_memory import AssociativeMemoryBank
from concordia.components import agent as agent_components
from concordia.typing.prefab import Prefab

from silisocs.adapters.concordia import SocialConcatActComponent
from silisocs.runtime.language_models import LanguageModel

from .mastodon_action_suggester import (
    MastodonActionSuggester,
)

OBSERVATION_TO_MEMORY_KEY = "__observation_to_memory__"
ELECTION_INFO_KEY = "__Election Information__"
INSTRUCTIONS_COMPONENT_KEY = "__Roleplaying Instructions__"
DEFAULT_ROLEPLAYING_INSTRUCTIONS = (
    "You are {name}. Act naturally and stay consistent with your persona and current goal."
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
class Entity(Prefab):
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
    model: LanguageModel,
    memory_bank: AssociativeMemoryBank,
) -> ComponentEntity:
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
    roleplaying = str(
        self.params.get("roleplaying_instructions", DEFAULT_ROLEPLAYING_INSTRUCTIONS)
        or DEFAULT_ROLEPLAYING_INSTRUCTIONS
    )
    instructions = agent_components.instructions.Instructions(
        agent_name=agent_name,
        pre_act_label="\nInstructions",
        state=roleplaying.format(name=agent_name),
    )

    election_info_key = "Election Information"
    election_information = agent_components.constant.Constant(
        state=(election_info),
        pre_act_key="Critical election information\n",
    )

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

    self_perception_key = "SelfPerception"
    self_perception = agent_components.question_of_recent_memories.SelfPerception(
        model=model,
        add_to_memory=False,
        pre_act_key=self_perception_key,
    )

    action_suggester_key = "Mastodon Action Suggestion"
    action_suggester = MastodonActionSuggester(
        model=model,
        action_probabilities=ACTION_PROBABILITIES,
    )
    components_of_agent = {
        # Components that provide pre_act context.
        instructions_key: instructions,
        election_info_key: election_information,
        OBSERVATION_TO_MEMORY_KEY: observation_to_memory,
        agent_components.observation.DEFAULT_OBSERVATION_COMPONENT_KEY: (observation),
        agent_components.memory.DEFAULT_MEMORY_COMPONENT_KEY: (
            agent_components.memory.AssociativeMemory(memory_bank=memory_bank)
        ),
        self_perception_key: self_perception,
        action_suggester_key: action_suggester,
    }

    component_order = list(components_of_agent.keys())
    if overarching_goal is not None:
        if goal_label is not None:
            components_of_agent[goal_label] = overarching_goal
            component_order.insert(1, goal_label)

    act_component = SocialConcatActComponent(
        model=model,
        component_order=component_order,
        randomize_choices=randomize_choices,
    )

    agent = ComponentEntity(
        agent_name=agent_name,
        act_component=act_component,
        context_components=components_of_agent,
    )

    return agent
