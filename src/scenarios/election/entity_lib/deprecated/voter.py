from inspect import signature

from agent_utils.base_agent import BaseAgentBuilder
from concordia.components import agent as ext_components

# Default probabilities for different Mastodon operations
ACTION_PROBABILITIES = {
    # High frequency actions
    "like_toot": 0.2,  # Most common action
    "boost_toot": 0.2,  # Common but less than likes
    "toot": 0.2,  # 0.20,  # Regular posting
    "reply": 0.4,  # 0.15,
    # Medium frequency actions
    "follow": 0.0,  # Following new accounts
    "unfollow": 0.00,  # 25,  # Unfollowing accounts
    "print_timeline": 0.0,  # Reading timeline
    # Low frequency actions
    "block_user": 0.0,  # Blocking problematic users
    "unblock_user": 0.0,  # Unblocking users
    "delete_posts": 0.0,  # Deleting own posts
    "update_bio": 0.0,  # Updating profile
    "print_notifications": 0.00,  # 25,  # Checking notifications
}

NUM_MEMORIES = 10


# define custom component classes
class RelevantOpinions(
    ext_components.question_of_query_associated_memories.QuestionOfQueryAssociatedMemoriesWithoutPreAct
):
    def __init__(self, name, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = name


class OpinionsOnCandidate(ext_components.question_of_recent_memories.QuestionOfRecentMemories):
    def __init__(self, name, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = name


# derive build class, filling out agent-specific methods
class AgentBuilder(BaseAgentBuilder):
    @classmethod
    def add_custom_components(
        cls,
        model,
        measurements,
        base_components,
        base_component_order,
        custom_component_config,
    ):
        # parse settings
        setting_description = custom_component_config["setting_description"]
        agent_name = custom_component_config["agent_name"]
        candidates = [
            candidate_dict["name"]
            for partisan_type, candidate_dict in custom_component_config["setting_details"][
                "candidate_info"
            ].items()
        ]

        # labels of custom components and common settings
        names = [
            [
                "ElectionInformation",
                "## CRITICAL ELECTION INFORMATION\n",
            ],  # cls._get_component_name()=ElectionInformation
            [
                candidates[0] + "RelevantOpinion",
                f"{agent_name} thinks of {candidates[0]} as",
            ],  # cls._get_component_name()=RelevantOpinion
            [
                candidates[1] + "RelevantOpinion",
                f"{agent_name} thinks of {candidates[1]} as",
            ],  # cls._get_component_name()=RelevantOpinion
            [
                candidates[0] + "OpinionOnCandidate",
                f"Recent thoughts of candidate {candidates[0]}",
            ],  # cls._get_component_name()=OpinionOnCandidate
            [
                candidates[1] + "OpinionOnCandidate",
                f"Recent thoughts of candidate {candidates[1]}",
            ],  # cls._get_component_name()=OpinionOnCandidate
        ]
        pre_act_keys_dict = {name: pre_act_key for name, pre_act_key in names}
        component_order = [item[0] for item in names]
        dependencies = {
            candidate + "OpinionOnCandidate": {
                "SelfPerception": "Persona Information",
                candidate
                + "RelevantOpinion": f"{agent_name}'s opinion of candidate {candidate}",  # why not pre_Act_key here?
            }
            for candidate in candidates
        }

        # instantiate components
        z = {}
        for name, pre_act_key in pre_act_keys_dict.items():
            settings = {}

            # add generic options
            settings["logging_channel"] = measurements.get_channel(name).on_next
            settings["pre_act_key"] = pre_act_key

            # instantiate components. Add component-specific settings first
            if name == "ElectionInformation":
                settings["state"] = setting_description
                component_constructor = ext_components.constant.Constant
            else:
                settings["add_to_memory"] = False
                settings["num_memories_to_retrieve"] = NUM_MEMORIES
                settings["name"] = name
                for candidate in candidates:
                    if name == candidate + "RelevantOpinion":
                        settings["queries"] = [f"policies and actions of {candidate}"]
                        settings["question"] = (
                            f"Given the following statements, what does {agent_name} think of the {{query}}?"
                        )
                        settings["model"] = model
                        component_constructor = RelevantOpinions
                    elif name == candidate + "OpinionOnCandidate":
                        settings["answer_prefix"] = (
                            f"{agent_name}'s current opinion on candidate {candidate} is"
                        )
                        settings["question"] = "".join(
                            [
                                f"Given {agent_name}'s opinion about candidate {candidate}, and the recent observations,",
                                f"what are some current thoughts that {agent_name} is having about candidate {candidate}? ",
                                "Consider how recent observations may or may not have changed this opinion based of the persona of the agent.",
                            ]
                        )
                        component_constructor = OpinionsOnCandidate

            # check for and add dependencies
            if name in dependencies:
                settings["components"] = dependencies[name]
            if "model" in signature(component_constructor.__init__).parameters.keys():
                settings["model"] = model
            if "model" in signature(component_constructor.__bases__[0].__init__).parameters.keys():
                settings["model"] = model
            z[name] = component_constructor(**settings)

        # set order: base then custom, but election information first, and action suggester last
        component_order = base_component_order[:-1] + component_order + [base_component_order[-1]]
        return z | base_components, component_order

    @classmethod
    def get_suggested_action_probabilities(cls) -> dict[str, float]:
        return ACTION_PROBABILITIES
