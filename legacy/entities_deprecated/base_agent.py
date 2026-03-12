import ast
import datetime
import importlib
import json
import random
import re
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from inspect import signature
from typing import Any

import numpy as np
from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import (
    associative_memory,
    formative_memories,
)
from concordia.clocks import game_clock
from concordia.components import agent as ext_components
from concordia.components.agent import action_spec_ignored
from concordia.document import interactive_document
from concordia.language_model import language_model
from concordia.memory_bank import legacy_associative_memory
from concordia.typing import clock as gc
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component, logging
from concordia.utils import helper_functions
from concordia.utils import measurements as measurements_lib
from sim.sim_utils.misc_sim_utils import ConfigStore

DEFAULT_PRE_ACT_KEY = "Action"
DEFAULT_ACTION_PROBABILITIES = {
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
RECENT_MEMORY_WINDOW_IN_HOURS = 4


class AllActComponent(entity_component.ActingComponent):
    def __init__(
        self,
        model: language_model.LanguageModel,
        clock: gc.GameClock,
        component_order: Sequence[str] | None = None,
        pre_act_key: str = DEFAULT_PRE_ACT_KEY,
        logging_channel: logging.LoggingChannel = logging.NoOpLoggingChannel,
    ):
        self._model = model
        self._clock = clock
        if component_order is None:
            self._component_order = None
        else:
            self._component_order = tuple(component_order)
        if self._component_order is not None:
            if len(set(self._component_order)) != len(self._component_order):
                raise ValueError(
                    "The component order contains duplicate components: "
                    + ", ".join(self._component_order)
                )

        self._pre_act_key = pre_act_key
        self._logging_channel = logging_channel

    def _context_for_action(
        self,
        contexts: entity_component.ComponentContextMapping,
    ) -> str:
        if self._component_order is None:
            return "\n".join(context for context in contexts.values() if context)
        order = self._component_order + tuple(
            sorted(set(contexts.keys()) - set(self._component_order))
        )
        return "\n\n".join(contexts[name] for name in order if contexts.get(name, False))

    def get_action_attempt(
        self,
        contexts: entity_component.ComponentContextMapping,
        action_spec: entity_lib.ActionSpec,
    ) -> str:
        prompt = interactive_document.InteractiveDocument(self._model)
        context = self._context_for_action(contexts)
        prompt.statement(context + "\n")

        call_to_action = action_spec.call_to_action.format(
            name=self.get_entity().name,
            timedelta=helper_functions.timedelta_to_readable_str(self._clock.get_step_size()),
        )

        prefix = self.get_entity().name + " "
        prefix = "STEP 1: " + prefix if action_spec.tag == "phone" else prefix
        if action_spec.output_type == entity_lib.OutputType.FREE:
            if not action_spec.tag == "media":
                if action_spec.tag == "phone":
                    pattern = r"\[observation\] (?:\[Action done on phone\]|) (.*?)(?=\[observation\]|## CURRENT DATE AND TIME|$)"
                    matches = re.findall(pattern, context, re.DOTALL)
                    # Format the output with numbering
                    numbered_output = "\n".join(
                        [f"{i + 1}. {match.strip()}" for i, match in enumerate(matches)]
                    )
                    actions_conducted = "\nRecently taken actions:\n" + numbered_output + "\n"
                    # cot_call = (
                    #     " ".join(
                    #         [
                    #             "Think step-by-step on what single action {name} should now take,",
                    #             "based on the instructions and the information about {name} structured in the CAPITALIZED sections above these instructions. "
                    #             "Choose the action suggested in [Suggested Action], unless doing so goes against the following detailed instructions:\n",
                    #         ]
                    #     ).format(name=self.get_entity().name)
                    #     + call_to_action
                    #     # + actions_conducted
                    # )
                    output = prefix
                    output += prompt.open_question(
                        call_to_action + actions_conducted,  # cot_call,  #
                        max_tokens=500,
                        answer_prefix=output,
                        answer_label="Answer",
                        # This terminator protects against the model providing extra context
                        # after the end of a directly spoken response, since it normally
                        # puts a space after a quotation mark only in these cases.
                        terminators=(),  #'" ', "\n"),
                        question_label="",  # Action Decision",
                    )
                    thoughts = "Current thought on action to take: " + output + "\n"
                    prompt.statement(thoughts)
                else:
                    output = prefix
                    output += prompt.open_question(
                        call_to_action,
                        max_tokens=2200,
                        answer_prefix=output,
                        answer_label="Answer",
                        # This terminator protects against the model providing extra context
                        # after the end of a directly spoken response, since it normally
                        # puts a space after a quotation mark only in these cases.
                        terminators=(),  #'" ', "\n"),
                        question_label="Exercise",
                    )
            else:
                media_str, call_to_action = call_to_action.split("Context", 1)
                call_to_action = "Context" + call_to_action
                media_list = ast.literal_eval(media_str.strip())
                output = prefix
                output += self._model.sample_text(
                    prompt=context + "\n" + call_to_action,  # order correct?
                    media=media_list,
                )
            self._log(output, prompt)
            return output
        if action_spec.output_type == entity_lib.OutputType.CHOICE:
            idx = prompt.multiple_choice_question(
                question=call_to_action, answers=action_spec.options
            )
            output = action_spec.options[idx]
            self._log(output, prompt)
            return output
        if action_spec.output_type == entity_lib.OutputType.FLOAT:
            sampled_text = prompt.open_question(
                call_to_action,
                max_tokens=2200,
                answer_prefix=prefix,
            )
            self._log(sampled_text, prompt)
            try:
                return str(float(sampled_text))
            except ValueError:
                return "0.0"
        else:
            raise NotImplementedError(
                f"Unsupported output type: {action_spec.output_type}. "
                "Supported output types are: FREE, CHOICE, and FLOAT."
            )

    def _log(self, result: str, prompt: interactive_document.InteractiveDocument):
        self._logging_channel(
            {
                "Key": self._pre_act_key,
                "Value": result,
                "Prompt": prompt.view().text().splitlines(),
            }
        )


class ActionSuggester(action_spec_ignored.ActionSpecIgnored):
    """Suggests likely platform operations (here Mastodon) for an agent to perform."""

    def __init__(
        self,
        model: language_model.LanguageModel,
        action_probabilities: dict[str, float] | None = None,
        pre_act_key: str = "[Suggested Action]",
        logging_channel: logging.LoggingChannel = logging.NoOpLoggingChannel,
    ):
        """Initialize the action suggester component.

        Args:
            model: The language model to use.
            action_probabilities: Optional dictionary mapping action names to their
                probabilities. If not provided, uses DEFAULT_ACTION_PROBABILITIES.
            pre_act_key: Key to identify component output in pre_act.
            logging_channel: Channel for logging component behavior.

        Raises
        ------
            ValueError: If probabilities don't sum to exactly 1.0 or if invalid actions provided
        """
        super().__init__(pre_act_key)
        self._model = model
        self._logging_channel = logging_channel

        # Use provided probabilities or defaults
        self._action_probs = action_probabilities or DEFAULT_ACTION_PROBABILITIES.copy()

        # Validate probabilities and actions
        self._validate_probabilities(self._action_probs)

        # Store last suggestion for consistency within same context
        self._last_suggestion: str | None = None

    @staticmethod
    def _validate_probabilities(probs: dict[str, float]) -> None:
        """Validate the probability configuration.

        Args:
            probs: Dictionary of action probabilities to validate

        Raises
        ------
            ValueError: If probabilities are invalid or don't sum to 1.0
        """
        # Check for valid actions
        valid_actions = set(DEFAULT_ACTION_PROBABILITIES.keys())
        invalid_actions = set(probs.keys()) - valid_actions
        if invalid_actions:
            raise ValueError(
                f"Invalid actions provided: {invalid_actions}. Valid actions are: {valid_actions}"
            )

        # Check for negative probabilities
        negative_probs = {k: v for k, v in probs.items() if v < 0}
        if negative_probs:
            raise ValueError(f"Negative probabilities not allowed: {negative_probs}")

        # Sum probabilities using Decimal for precise comparison
        total = Decimal(0)
        for prob in probs.values():
            total += Decimal(str(prob)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)

        if total != Decimal(1):
            raise ValueError(
                f"Action probabilities must sum to exactly 1.0 (got {float(total)}). "
                "Please adjust probabilities to ensure they sum to 100%."
            )

    def _select_action(self) -> str:
        """Randomly select an action based on configured probabilities."""
        rand_val = random.random()
        cumulative_prob = 0.0

        for action, prob in self._action_probs.items():
            cumulative_prob += prob
            if rand_val <= cumulative_prob:
                return action

        # Fallback to most common action if we somehow don't select one
        return "like_toot"

    def _make_pre_act_value(self) -> str:
        """Generate a suggestion for the next Mastodon action."""
        # If we already have a suggestion for this context, return it
        if self._last_suggestion is not None:
            return self._last_suggestion

        agent_name = self.get_entity().name
        selected_action = self._select_action()

        # Create natural language suggestions for different action types
        action_descriptions = {
            "like_toot": f"{agent_name} feels inclined to like someone's post",
            "boost_toot": f"{agent_name} considers boosting a post they appreciate",
            "toot": f"{agent_name} has something they might want to post about",
            "reply": f"{agent_name} considers replying to a post",
            "follow": f"{agent_name} thinks about following a new account",
            "unfollow": f"{agent_name} considers unfollowing an account",
            "print_timeline": f"{agent_name} feels like checking their timeline",
            "block_user": f"{agent_name} contemplates blocking a problematic user",
            "unblock_user": f"{agent_name} considers unblocking someone",
            "delete_posts": f"{agent_name} considers deleting some old posts",
            "update_bio": f"{agent_name} feels like updating their profile",
            "print_notifications": f"{agent_name} wants to check their notifications",
        }

        result = action_descriptions.get(
            selected_action, f"{agent_name} considers interacting with Mastodon"
        )

        # Store suggestion for consistency
        self._last_suggestion = result

        # Log the suggestion
        self._logging_channel(
            {
                "Key": self.get_pre_act_key(),
                "Value": result,
                "Selected action": selected_action,
                "Action probabilities": self._action_probs,
            }
        )

        print(f"\n[Action Suggester] Returning: {result}")
        return result

    def get_state(self) -> str:
        """Get the component's state as a string for the agent's context."""
        result = self._make_pre_act_value()
        print(f"\n[Action Suggester] Adding to {self.get_entity().name}'s context: {result}")
        return result

    def set_state(self, state: dict[str, dict[str, float]]) -> None:
        """Set the component's state."""
        if "action_probabilities" in state:
            self._validate_probabilities(state["action_probabilities"])
            self._action_probs = state["action_probabilities"]
            self._last_suggestion = None  # Reset suggestion when probabilities change


# -------base_agent_model.py----
from abc import ABC, abstractmethod


class BaseAgentBuilder(ABC):
    """Base class that provides function inheritance without instantiation"""

    @classmethod
    @abstractmethod
    def add_custom_components(
        cls,
        model,
        measurements,
        base_components: dict[str, Any],
        base_component_names: list[str],
        custom_component_config: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Example of a base operation that all children must implement"""

    @classmethod
    @abstractmethod
    def get_suggested_action_probabilities(cls) -> dict:
        """Example of a base operation that all children must implement"""

    @classmethod
    def build(
        cls,
        *,
        config: formative_memories.AgentConfig,
        model: language_model.LanguageModel,
        memory: associative_memory.AssociativeMemory,
        clock: game_clock.MultiIntervalClock,
        update_time_interval: datetime.timedelta | None = None,
        input_data: dict[str, Any] = {},
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        """Build an agent.

        Args:
            config: The agent config to use.
            model: The language model to use.
            memory: The agent's memory object.
            clock: The clock to use.
            update_time_interval: Unused (but required by the interface for now)
            role_and_setting_config:
                -name
                -agent_name
                -description

        Returns
        -------
            An agent.
        """
        del update_time_interval

        # parse settings
        agent_name = config.name
        assert agent_name == input_data["agent_name"], "agent names not same!"
        goal = config.goal
        post_style = input_data["style"]
        cfg = ConfigStore.get_config()

        raw_memory = legacy_associative_memory.AssociativeMemoryBank(memory)
        measurements = measurements_lib.Measurements()

        names, pre_act_key_dict, idx_order = [], {}, []
        for component in cfg.agents.base_agent.components:
            names.append(component.name)
            pre_act_key_dict[component.name] = component.pre_act_key
            idx_order.append(component.seq_index)
        component_order = [names[idx] for idx in idx_order]

        dependencies = cfg.agents.base_agent.dependencies

        # Instantiate components
        z = {}
        for name, pre_act_key in pre_act_key_dict.items():
            settings = {}

            # add generic options
            settings["logging_channel"] = measurements.get_channel(name).on_next
            settings["pre_act_key"] = pre_act_key

            # Add component-specific settings and assign constructor
            if name == "Instructions":
                settings["agent_name"] = agent_name
                settings["state"] = cfg.sim.roleplaying_instructions
                settings["state"] = settings["state"].format(
                    name=agent_name
                )  # component_constructor = ext_components.instructions.Instructions
                Instructions = importlib.import_module(
                    "sim.agent_utils.components.instructions"
                ).Instructions
                component_constructor = Instructions
            elif name == "OverarchingGoal":
                settings["state"] = goal
                component_constructor = ext_components.constant.Constant
            elif name == "Observation":
                settings["clock_now"] = clock.now
                settings["timeframe"] = clock.get_step_size()
                component_constructor = ext_components.observation.Observation
            elif name == "ObservationSummary":
                settings["clock_now"] = clock.now
                settings["timeframe_delta_from"] = datetime.timedelta(
                    hours=RECENT_MEMORY_WINDOW_IN_HOURS
                )
                settings["timeframe_delta_until"] = datetime.timedelta(hours=0)
                component_constructor = ext_components.observation.ObservationSummary
            elif name == "TimeDisplay":
                settings["function"] = clock.current_time_interval_str
                component_constructor = ext_components.report_function.ReportFunction
            elif name == "AllSimilarMemories":
                settings["num_memories_to_retrieve"] = NUM_MEMORIES
                component_constructor = ext_components.all_similar_memories.AllSimilarMemories
            elif name == "IdentityWithoutPreAct":
                component_constructor = (
                    ext_components.question_of_query_associated_memories.IdentityWithoutPreAct
                )
                settings["model"] = model
            elif name == "SelfPerception":
                component_constructor = ext_components.question_of_recent_memories.SelfPerception
            elif name == "ActionSuggester":
                settings["action_probabilities"] = cls.get_suggested_action_probabilities()
                component_constructor = ActionSuggester
            elif name == "PostingStyle":
                settings["state"] = post_style
                component_constructor = ext_components.constant.Constant
            else:
                exit("no matching component class found")
            # check for and add dependencies
            if name in dependencies:
                settings["components"] = dependencies[name]
            if "model" in signature(component_constructor.__init__).parameters.keys():
                settings["model"] = model
            if "model" in signature(component_constructor.__bases__[0].__init__).parameters.keys():
                settings["model"] = model

            # instantiate
            z[name] = component_constructor(**settings)

        # add custom components
        # n.b. custom components can be interleaved so reassign order
        z, component_order = cls.add_custom_components(
            model, measurements, z, component_order, input_data
        )

        # last component is the memory
        memory_model = ext_components.memory_component
        memory_component = memory_model.MemoryComponent(raw_memory)
        memory_component_name = memory_model.DEFAULT_MEMORY_COMPONENT_NAME
        z[memory_component_name] = memory_component
        component_order.append(memory_component_name)

        # instantiate act component
        act_component = AllActComponent(
            model=model,
            clock=clock,
            component_order=component_order,
            logging_channel=measurements.get_channel("ActComponent").on_next,
        )
        # and finally the agent
        agent = entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=agent_name,
            act_component=act_component,
            context_components=z,
            component_logging=measurements,
            config=config,
        )

        return agent

    @classmethod
    def rebuild_from_json(
        cls,
        json_data: str,
        model: language_model.LanguageModel,
        embedder: Callable[[str], np.ndarray],
        clock: game_clock.MultiIntervalClock,
        input_data: dict[str, Any] = {},
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        """Rebuilds an agent from JSON data."""
        data = json.loads(json_data)

        new_agent_memory = associative_memory.AssociativeMemory(
            sentence_embedder=embedder,
            clock=clock.now,
            clock_step_size=clock.get_step_size(),
        )

        if "agent_config" not in data:
            raise ValueError("The JSON data does not contain the agent config.")
        agent_config = formative_memories.AgentConfig.from_dict(data.pop("agent_config"))

        agent = cls.build(
            config=agent_config,
            model=model,
            memory=new_agent_memory,
            clock=clock,
            input_data=input_data,
        )
        print("Rebuilding agent from JSON data")
        for component_name in agent.get_all_context_components():
            print(component_name, data[component_name])
            agent.get_component(component_name).set_state(data.pop(component_name))

        agent.get_act_component().set_state(data.pop("act_component"))

        assert not data, f"Unused data {sorted(data)}"
        return agent


def save_agent_to_json(
    agent: entity_agent_with_logging.EntityAgentWithLogging,
) -> str:
    """Saves an agent to JSON data.

    This function saves the agent's state to a JSON string, which can be loaded
    afterwards with `rebuild_from_json`. The JSON data
    includes the state of the agent's context components, act component, memory,
    agent name and the initial config. The clock, model and embedder are not
    saved and will have to be provided when the agent is rebuilt. The agent must
    be in the `READY` phase to be saved.

    Args:
        agent: The agent to save.

    Returns
    -------
        A JSON string representing the agent's state.

    Raises
    ------
        ValueError: If the agent is not in the READY phase.
    """
    if agent.get_phase() != entity_component.Phase.READY:
        raise ValueError("The agent must be in the `READY` phase to be saved.")

    data = {
        component_name: agent.get_component(component_name).get_state()
        for component_name in agent.get_all_context_components()
    }

    data["act_component"] = agent.get_act_component().get_state()

    config = agent.get_config()
    if config is not None:
        data["agent_config"] = config.to_dict()

    return json.dumps(data)
