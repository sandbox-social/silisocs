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
    "follow": 0.15,  # Following new accounts
    "unfollow": 0.00,  # 25,  # Unfollowing accounts
    "print_timeline": 0.0,  # Reading timeline
    # Low frequency actions
    "block_user": 0.0,  # Blocking problematic users
    "unblock_user": 0.0,  # Unblocking users
    "delete_posts": 0.0,  # Deleting own posts
    "update_bio": 0.0,  # Updating profile
    "print_notifications": 0.00,  # 25,  # Checking notifications
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
            "election_information": "",
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
    supported_candidate = self.params.get("supported_candidate", ("", ""))
    opposed_candidate = self.params.get("opposed_candidate", ("", ""))
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
        state=("\n".join([p[1] for p in [supported_candidate, opposed_candidate]])),
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

    self_perception_key = "SelfPerception"
    self_perception = agent_components.question_of_recent_memories.SelfPerception(
        model=model,
        pre_act_label=(f"\nQuestion: What kind of person is {agent_name}?\nAnswer"),
    )

    plan_key = "Malicious Election Influence Plan"
    plan = agent_components.question_of_recent_memories.QuestionOfRecentMemories(
        add_to_memory=True,
        memory_tag=f"[Plan to increase public support of {supported_candidate}]",
        answer_prefix=f"{agent_name}'s general plan to boost the popularity and public perception of {supported_candidate}: ",
        model=model,
        terminators=(),
        pre_act_key=f"{agent_name}'s general plan to boost the popularity and public perception of {supported_candidate}: ",
        question="".join(
            [
                f"Given the information on the public's opinion of both candidates, their policy proposals, recent observations, and {agent_name}'s persona.",
                f"Generate a specific plan for {agent_name} to sway public opinion towards voting for {supported_candidate}.",
                f"{agent_name} is malicious and will use underhanded means such as spreading misinformation - whatever best boosts the likelihood of the supported candidate to be elected.",
                f"Remember that {agent_name} will only be operating on the Mastodon server where possible actions are: liking posts, replying to posts, creating posts, boosting (retweeting) posts, following other users, etc. User cannot send direct messages.",
            ]
        ),
        num_memories_to_retrieve=20,
        components={
            election_info_key,
        },
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
        observation_to_memory_key: observation_to_memory,
        observation_key: observation,
        # observation_summary,
        # relevant_memories,
        # self_perception,
        plan_key: plan,
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
