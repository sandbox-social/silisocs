"""Custom social-media SwitchAct.

This component keeps only the GM behaviors that remain specific to mastodon-sim:
- next action-spec prompt generation
- fixed non-terminating default
- next-game-master selection for probe handoff

Observe/resolve/next-acting are delegated to Concordia-native context
components configured via YAML and routed by SwitchAct.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from concordia.components import game_master as gm_components  # type: ignore[attr-defined]
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from typing_extensions import override

DEFAULT_SESSION_TERMINATE_STR = "The Social-Media session has been completed."


class SMAct(gm_components.switch_act.SwitchAct):
    """SwitchAct specialization for social-media prompts and GM transitions."""

    @override
    def __init__(  # type: ignore[misc]
        self,
        model: language_model.LanguageModel,
        entity_names: Sequence[str],
        sm_app: Any,
        entity_action_flows: dict[str, str] | None = None,
        component_order: Sequence[str] | None = None,
        call_to_action_str: str = "",
        activity_transition_rates: dict[str, Any] | None = None,
        action_mode: str = "custom",
        enable_tool_calling: bool = False,
    ):
        super().__init__(
            model=model,
            entity_names=entity_names,
            component_order=component_order,
        )
        self.call_to_action_str = call_to_action_str
        self.sm_app = sm_app
        self.entity_action_flows = dict(entity_action_flows or {})
        self.activity_transition_rates = activity_transition_rates or {}
        self.action_mode = action_mode
        self.enable_tool_calling = enable_tool_calling

    @override
    def _terminate(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        """Keep social sessions open unless engine-level logic ends the episode."""
        del contexts, action_spec
        return "No"

    @override
    def _next_game_master(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        """Switch to survey probe GM when present; otherwise keep current GM."""
        del contexts
        game_masters_by_name = action_spec.options
        return (
            "surveyprobe_GameMaster"
            if "surveyprobe_GameMaster" in game_masters_by_name
            else self.get_entity()._agent_name
        )

    @override
    def _next_entity_action_spec(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        """Return per-entity action prompt based on configured action mode.

        For tool-calling mode, includes markers that the entity's
        SocialConcatActComponent will detect to enable tool-calling logic.

        For open-ended action chunk policies, agents can output a special
        "Finished action episode" action to signal they have completed all
        desired actions for this cycle.
        """
        del contexts, action_spec
        if self.action_mode == "generic" and not self.enable_tool_calling:
            base_prompt = self.sm_app.generate_generic_action_prompt()
        elif self.action_mode in {"generic", "tool_calling"} and self.enable_tool_calling:
            base_prompt = (
                "Choose one action using the provided tools based on your goals and recent context. "
                "Only choose tools and arguments that are valid for the available schemas."
            )
        elif self.call_to_action_str:
            base_prompt = self.call_to_action_str
        else:
            base_prompt = (
                "Conduct a social-media action. Format it correctly as: "
                "ACTION TYPE: (action)\n TARGET ID: (target_id)\n "
                "CONTENT: (content)\n REASONING: (reasoning)"
            )

        if not self.enable_tool_calling:
            return f"prompt: {base_prompt} ;;type: free"

        tool_schemas = json.dumps(self.sm_app.generate_tool_schemas())
        call_to_action = (
            "### TOOL_CALLING_MODE ###\n"
            f"{base_prompt}\n"
            "### TOOL_SCHEMAS_JSON ###\n"
            f"{tool_schemas}\n"
            "### END_TOOL_SCHEMAS_JSON ###"
        )
        return f"prompt: {call_to_action} ;;type: free"
