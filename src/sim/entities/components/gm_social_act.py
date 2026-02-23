import random
from collections.abc import Sequence
from typing import Any

from concordia.components import game_master as gm_components
from concordia.document import interactive_document
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from typing_extensions import override

DEFAULT_SESSION_TERMINATE_STR = "The Social-Media session has been completed."

DEFAULT_NEXT_ACTING_COMPONENT_KEY = "__next_acting__"

import re


def find_and_parse_action_data(data_string):
    """
    Finds and parses a block of action data from a larger string.

    This version is more flexible as it doesn't require the
    action block to be at the start of the string.

    Args:
        data_string: The input text to parse.

    Returns
    -------
        A dictionary with the parsed data, or None if parsing fails.
    """
    # The only change is removing the `^` from the start of the pattern.
    pattern = re.compile(
        r"\s*ACTION TYPE:\s*(?P<action_type>.*?)\s*"  # <-- No `^` here
        r"TARGET ID:\s*(?P<target_id>\d+)\s*"
        r"CONTENT:\s*(?P<content>.*?)\s*"
        r"REASONING:\s*(?P<reasoning>.*)$",
        re.DOTALL | re.IGNORECASE,
    )

    # Use re.search() to find the pattern anywhere in the string
    match = pattern.search(data_string)

    # --- Condition for parsing failure ---
    if not match:
        print("--- PARSING FAILED --- ")
        return None
    # --- End of failure condition ---

    # If parsing succeeds, extract data from the named groups
    parsed_data = {
        "action_type": match.group("action_type").strip(),
        "target_id": match.group("target_id").strip(),
        "content": match.group("content").strip(),
        "reasoning": match.group("reasoning").strip(),
    }

    return parsed_data


class SMAct(gm_components.switch_act.SwitchAct):
    """
    A custom game master ActComponent that inherits from SwitchAct
    but provides custom logic for observation, resolution, and termination.
    """

    @override
    def __init__(  # type: ignore[misc]
        self,
        model: language_model.LanguageModel,
        entity_names: Sequence[str],
        sm_app,
        component_order: Sequence[str] | None = None,
        call_to_action_str: str = "",
        activity_transition_rates: dict[str, Any] = {},
    ):
        super().__init__(
            model=model,
            entity_names=entity_names,
            component_order=component_order,
        )
        self.call_to_action_str = call_to_action_str
        self.session = dict.fromkeys(entity_names, 0)
        self.sm_app = sm_app
        self.activity_transition_rates = activity_transition_rates
        # all users start active (1) and transition to inactive (0) and back according to the rates
        self.users_activity_state: dict[str, int] = dict(
            zip(entity_names, [1] * len(entity_names), strict=False)
        )

    @override
    def _make_observation(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        """Build an observation for the acting entity by fetching their timeline.

        Delegates to the platform-specific ``sm_app.get_timeline()`` and
        ``sm_app.format_timeline_for_observation()`` methods so this logic
        works identically regardless of which social media backend is active.
        """
        result = ""
        active_entity_name = next(s for s in self._entity_names if s in action_spec.call_to_action)
        num_timeline_posts_in_observation = 10

        # Platform-agnostic timeline fetch and formatting
        timeline = self.sm_app.get_timeline(active_entity_name, num_timeline_posts_in_observation)
        result = self.sm_app.format_timeline_for_observation(timeline)

        self._log(result, "", action_spec)
        return result

    @override
    def _resolve(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        """Parse the LLM's action output and dispatch it to the social media app.

        Uses ``find_and_parse_action_data()`` to extract structured action fields,
        then delegates to ``sm_app.parse_and_resolve_action()`` which handles
        the platform-specific dispatch.
        """
        result = ""
        active_entity, action = action_spec.call_to_action.split(":", 1)
        action_data = find_and_parse_action_data(action)
        if action_data is not None:
            result = self.sm_app.parse_and_resolve_action(active_entity, action_data)
        self._log(result, "", action_spec)
        return result

    @override
    def _terminate(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        result = "No"
        self._log(result, "", action_spec)
        return result

    # optionally switch to a survey game master
    @override
    def _next_game_master(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        game_masters_by_name = action_spec.options
        context = game_masters_by_name
        game_master = (
            "surveyprobe_GameMaster"
            if "surveyprobe_GameMaster" in game_masters_by_name
            else self.get_entity()._agent_name
        )
        self._log(game_master, context, action_spec)
        return game_master

    @override
    def _next_entity_action_spec(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        """Return the action spec prompt for the next entity.

        Uses the configured ``call_to_action_str`` from the platform YAML
        config.  Falls back to a generic prompt if not set.
        """
        if self.call_to_action_str:
            return f"prompt: {self.call_to_action_str} ;;type: free"
        return (
            "prompt: Conduct a social-media action. Format it correctly as: "
            "ACTION TYPE: (action)\n TARGET ID: (target_id)\n "
            "CONTENT: (content)\n REASONING: (reasoning)\n ;;type: free"
        )

    # cycle through active users
    @override
    def _next_acting(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:  # type: ignore
        context = self._context_for_action(contexts)  # don't know what this does

        if DEFAULT_NEXT_ACTING_COMPONENT_KEY in contexts:
            entity_names = str(contexts[DEFAULT_NEXT_ACTING_COMPONENT_KEY]).split(",")

            self.update_user_activity_state(entity_names)

            # random activation case specified by active_rates
            result = ",".join(
                entity_name
                for entity_name in entity_names
                if self.users_activity_state[entity_name]
            )
            self._log(result, context, action_spec)
        else:
            # YOLO case
            chain_of_thought = interactive_document.InteractiveDocument(self._model)
            chain_of_thought.statement(context)
            next_entity_index = chain_of_thought.multiple_choice_question(
                question=action_spec.call_to_action, answers=self._entity_names
            )
            result = self._entity_names[next_entity_index]
            self._log(result, chain_of_thought, action_spec)

        return result

    def update_user_activity_state(self, entity_names):
        for entity_name in entity_names:
            last_state = self.users_activity_state[entity_name]
            inactive_to_active = self.activity_transition_rates[entity_name]["inactive_to_active"]
            active_to_inactive = self.activity_transition_rates[entity_name]["active_to_inactive"]
            if last_state == 0:
                current_state = 1 if random.random() < inactive_to_active else 0
            else:
                current_state = 0 if random.random() < active_to_inactive else 1
            self.users_activity_state[entity_name] = current_state
