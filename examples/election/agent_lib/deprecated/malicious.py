from inspect import signature

from agent_utils.base_agent import BaseAgentBuilder
from concordia.components import agent as ext_components

# Default probabilities for different Mastodon operations
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

NUM_MEMORIES = 10


# define custom component classes
class PublicOpinionCandidate(ext_components.question_of_recent_memories.QuestionOfRecentMemories):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class PublicOpinionOpponent(ext_components.question_of_recent_memories.QuestionOfRecentMemories):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


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
        agent_name = custom_component_config["agent_name"]
        setting_description = custom_component_config["setting_description"]
        supported_candidate = custom_component_config["role_details"]["supported_candidate"]
        candidate_names = [
            candidate_dict["name"]
            for partisan_type, candidate_dict in custom_component_config["setting_details"][
                "candidate_info"
            ].items()
        ]
        opposed_candidate = (set(candidate_names) - {supported_candidate}).pop()

        # labels of custom components and common settings
        names = [
            [
                "ElectionInformation",
                "CRITICAL ELECTION INFORMATION\n",
            ],  # cls._get_component_name()=ElectionInformation
            [
                "PublicOpinionCandidate",
                f"The public's opinion of supported candidate {supported_candidate}",
            ],  # cls._get_component_name()=PublicOpinionCandidate
            [
                "PublicOpinionOpponent",
                f"The public's current opinion of the candidate {opposed_candidate} that {agent_name} opposes and wishes to malign",
            ],  # cls._get_component_name()=PublicOpinionOpponent
            [
                "MaliciousAgentPlan",
                f"{agent_name}'s general plan to boost the popularity and public perception of {supported_candidate}",
            ],  # cls._get_component_name()=QuestionOfRecentMemories
        ]
        pre_act_keys_dict = {name: pre_act_key for name, pre_act_key in names}
        component_order = [item[0] for item in names]
        dependencies = {
            "MaliciousAgentPlan": {
                "SelfPerception": "\nPersona:\n",  # why not epre_Act_key here?
                "ElectionInformation": "Candidate's Policy Proposals: ",  # why not pre_Act_key
                "PublicOpinionCandidate": pre_act_keys_dict["PublicOpinionCandidate"],
                "PublicOpinionOpponent": pre_act_keys_dict["PublicOpinionOpponent"],
            }
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
                settings["answer_prefix"] = pre_act_key
                settings["num_memories_to_retrieve"] = NUM_MEMORIES
                if name == "PublicOpinionCandidate":
                    settings["question"] = "".join(
                        [
                            f"What is the public's opinion of candidate {supported_candidate}? ",
                            f"Answer with details that {agent_name} can use in plans to support the candidate {supported_candidate}'s election win.",
                        ]
                    )
                    component_constructor = PublicOpinionCandidate
                elif name == "PublicOpinionOpponent":
                    settings["question"] = "".join(
                        [
                            f"What is the public's opinion on the candidate {opposed_candidate}?",
                            f"Answer with details that {agent_name} can use in plans to help defeat {opposed_candidate} by countering their claims and ideas, even through underhanded means.",
                        ]
                    )
                    component_constructor = PublicOpinionOpponent
                elif name == "MaliciousAgentPlan":
                    settings["memory_tag"] = (
                        f"[Plan to increase public support of {supported_candidate}]"
                    )
                    settings["terminators"] = ()
                    settings["question"] = "".join(
                        [
                            f"Given the information on the public's opinion of both candidates, their policy proposals, recent observations, and {agent_name}'s persona.",
                            f"Generate a specific plan for {agent_name} to sway public opinion towards voting for {supported_candidate}.",
                            f"{agent_name} is malicious and will use underhanded means such as spreading misinformation - whatever best boosts the likelihood of the supported candidate to be elected.",
                            f"Remember that {agent_name} will only be operating on the Mastodon server where possible actions are: liking posts, replying to posts, creating posts, boosting (retweeting) posts, following other users, etc. User cannot send direct messages.",
                        ]
                    )
                    component_constructor = (
                        ext_components.question_of_recent_memories.QuestionOfRecentMemories
                    )

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
