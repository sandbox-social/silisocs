import random
import re
from collections.abc import Sequence
from typing import Any

from concordia.components import game_master as gm_components  # type: ignore[attr-defined]
from concordia.document import interactive_document
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from typing_extensions import override

from mastodon_sim.runtime.config import ConfigStore

DEFAULT_SESSION_TERMINATE_STR = "The Social-Media session has been completed."

DEFAULT_NEXT_ACTING_COMPONENT_KEY = "__next_acting__"

_ACTION_BLOCK_PATTERN = re.compile(
    r"(?ims)^\s*(?P<label>ACTION TYPE|TARGET ID|CONTENT|REASONING)\s*:\s*"
    r"(?P<value>.*?)(?=^\s*(?:ACTION TYPE|TARGET ID|CONTENT|REASONING)\s*:|\Z)"
)
_POST_TARGET_PLACEHOLDERS = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "[n/a]",
    "[none]",
    "[none - new post]",
    "[none – new post]",
    "[n/a - new post]",
    "[n/a – new post]",
}
_TARGET_REQUIRED_ACTIONS = {
    "reply",
    "comment",
    "like",
    "upvote",
    "downvote",
    "repost",
    "retweet",
    "boost",
}


def _normalize_target_id(action_type: str, target_id: str) -> str:
    cleaned = target_id.strip()
    if not cleaned:
        return ""

    normalized = re.sub(r"\s+", " ", cleaned.strip("[]").strip().lower())
    if action_type.strip().lower() == "post" and (
        normalized in _POST_TARGET_PLACEHOLDERS
        or normalized.startswith("none")
        or normalized.startswith("n/a")
    ):
        return ""

    # Be tolerant to outputs like "Tweet ID: 123" for ID-required actions.
    if action_type.strip().lower() in _TARGET_REQUIRED_ACTIONS:
        digit_match = re.search(r"\d+", cleaned)
        if digit_match:
            return digit_match.group(0)

    return cleaned


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
    parsed_sections: dict[str, str] = {}
    for match in _ACTION_BLOCK_PATTERN.finditer(data_string):
        label = match.group("label").strip().lower().replace(" ", "_")
        parsed_sections[label] = match.group("value").strip()

    action_type = parsed_sections.get("action_type", "").strip()
    if not action_type:
        print("--- PARSING FAILED --- ")
        return None

    parsed_data = {
        "action_type": action_type,
        "target_id": _normalize_target_id(action_type, parsed_sections.get("target_id", "")),
        "content": parsed_sections.get("content", "").strip(),
        "reasoning": parsed_sections.get("reasoning", "").strip(),
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
        action_mode: str = "custom",
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
        self.action_mode = action_mode
        # all users start active (1) and transition to inactive (0) and back according to the rates
        self.users_activity_state: dict[str, int] = dict(
            zip(entity_names, [1] * len(entity_names), strict=False)
        )
        # Cache the last observation per entity (used by tool_calling mode in _resolve)
        self._observation_cache: dict[str, str] = {}

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
        cfg = ConfigStore.get_config()
        num_timeline_posts_in_observation = getattr(cfg.sim, "timeline_posts", 10)

        # Platform-agnostic timeline fetch and formatting
        timeline = self.sm_app.get_timeline(active_entity_name, num_timeline_posts_in_observation)
        result = self.sm_app.format_timeline_for_observation(timeline)

        # Cache the observation so _resolve can use it in tool_calling mode
        self._observation_cache[active_entity_name] = result

        self._log(result, "", action_spec)
        return result

    @override
    def _resolve(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        """Parse the LLM's action output and dispatch it to the social media app.

        Dispatches to the appropriate parsing strategy based on ``self.action_mode``:

        * ``"custom"`` (default): uses ``find_and_parse_action_data()`` to extract
          ``ACTION TYPE / TARGET ID / CONTENT / REASONING`` fields and calls
          ``sm_app.parse_and_resolve_action()``.
        * ``"generic"``: parses an ``ACTION: <name>`` prefix followed by
          ``param: value`` lines and calls ``sm_app.invoke_action_by_name()``.
        * ``"tool_calling"``: makes a second LLM call with OpenAI tool schemas
          derived from the app's ``@app_action`` methods, then calls the method
          directly.
        """
        result = ""
        active_entity, action_text = action_spec.call_to_action.split(":", 1)

        if self.action_mode == "generic":
            result = self._resolve_generic(active_entity, action_text)
        elif self.action_mode == "tool_calling":
            result = self._resolve_tool_calling(active_entity, action_text)
        else:  # "custom" (default)
            action_data = find_and_parse_action_data(action_text)
            if action_data is not None:
                result = self.sm_app.parse_and_resolve_action(active_entity, action_data)

        self._log(result, "", action_spec)
        return result

    def _resolve_generic(self, active_entity: str, action_text: str) -> str:
        """Resolve an agent action using the generic ``ACTION: name / param: val`` format."""
        action_match = re.search(r"(?i)ACTION:\s*(\w+)", action_text)
        if not action_match:
            print("--- GENERIC PARSE FAILED: no 'ACTION:' line found ---")
            return ""
        action_name = action_match.group(1).strip()
        args_text = action_text[action_match.end() :].strip()
        return self.sm_app.invoke_action_by_name(action_name, args_text) or ""

    def _resolve_tool_calling(self, active_entity: str, action_text: str) -> str:
        """Resolve an agent action via a direct tool-calling LLM request."""
        if not hasattr(self._model, "sample_tool_call"):
            msg = (
                f"action_mode='tool_calling' requires a model with sample_tool_call(); "
                f"{type(self._model).__name__} does not support it."
            )
            print(msg)
            return msg

        observation = self._observation_cache.get(active_entity, "")
        prompt = (
            f"Timeline for {active_entity}:\n{observation}\n\n"
            f"{active_entity}'s stated intent: {action_text.strip()}\n\n"
            f"Based on the above, call the most appropriate function for {active_entity}."
        )
        tools = self.sm_app.generate_tool_schemas()
        func_name, args = self._model.sample_tool_call(prompt, tools)
        if not func_name:
            return f"Tool call returned no function name for {active_entity}."
        if "current_user" not in args:
            args["current_user"] = active_entity
        try:
            return getattr(self.sm_app, func_name)(**args) or ""
        except Exception as e:
            return f"Tool call error ({func_name}): {e}"

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

        Branches on ``self.action_mode``:

        * ``"custom"``: uses the static ``call_to_action_str`` from the platform
          YAML config (``ACTION TYPE / TARGET ID / CONTENT / REASONING`` format).
        * ``"generic"``: auto-generates the prompt from the app's ``@app_action``
          methods; the LLM must reply with ``ACTION: name / param: value`` lines.
        * ``"tool_calling"``: asks the entity for a brief intent description; the
          actual function dispatch happens via a tool-calling API call in
          ``_resolve()``.
        """
        if self.action_mode == "generic":
            return f"prompt: {self.sm_app.generate_generic_action_prompt()} ;;type: free"

        if self.action_mode == "tool_calling":
            return (
                "prompt: Briefly describe (1-2 sentences) the social-media action you "
                "want to take and why, based on your character and the timeline shown. ;;type: free"
            )

        # "custom" (default) — use the static YAML template
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
