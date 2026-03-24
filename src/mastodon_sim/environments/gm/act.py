"""Custom social-media SwitchAct components.

This module provides two SwitchAct variants:
1. SMAct: Simple, single-component-per-role (default)
2. MultiFlowSMAct: Multi-flow aware, routes entities to flow-specific components

Observe/resolve/next-acting are delegated to Concordia-native context
components configured via YAML and routed by these act components.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from concordia.components import game_master as gm_components  # type: ignore[attr-defined]
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from typing_extensions import override

DEFAULT_SESSION_TERMINATE_STR = "The Social-Media session has been completed."
_TOOL_SCHEMAS_START = "### TOOL_SCHEMAS_JSON ###"
_TOOL_SCHEMAS_END = "### END_TOOL_SCHEMAS_JSON ###"


class SMAct(gm_components.switch_act.SwitchAct):
    """SwitchAct specialization for social-media prompts and GM transitions (simple mode).

    Used when enable_gm_multi_flow=false. All entities use same component instances.
    """

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
        """Return per-entity action prompt based on configured action mode."""
        del contexts, action_spec

        # Step 1: Generate base prompt based on action_mode ONLY
        if self.action_mode == "generic":
            base_prompt = self.sm_app.generate_generic_action_prompt()
        elif self.call_to_action_str:
            base_prompt = self.call_to_action_str
        else:
            base_prompt = "Conduct a social-media action. Determine what ONE action would be most appropriate."

        # Step 2: If tool-calling is disabled, return prompt as-is
        if not self.enable_tool_calling:
            return f"prompt: {base_prompt} ;;type: free"

        # Step 3: If tool-calling is enabled, add tool schemas
        tool_schemas = []
        if hasattr(self.sm_app, "generate_tool_schemas"):
            tool_schemas = list(self.sm_app.generate_tool_schemas() or [])
        call_to_action = f"### TOOL_CALLING_MODE ###\n{base_prompt}"
        if tool_schemas:
            call_to_action += (
                f"\n{_TOOL_SCHEMAS_START}\n{json.dumps(tool_schemas)}\n{_TOOL_SCHEMAS_END}"
            )
        return f"prompt: {call_to_action} ;;type: free"


class MultiFlowSMAct(SMAct):
    """SwitchAct with multi-flow component routing.

    Used when enable_gm_multi_flow=true. Routes entities to flow-specific component
    instances based on their assigned action flow.

    Flow-to-component mapping structure:
        {
            "active": {"observe": "observe__timeline_make_observation",
                      "resolve": "resolve__parsed_action"},
            "fixed_pre": {"observe": "observe__episode_observation",
                         "resolve": "resolve__generic_action"}
        }

    Each entity's flow determines which component instances they use.
    """

    def __init__(  # type: ignore[misc]
        self,
        model: language_model.LanguageModel,
        entity_names: Sequence[str],
        sm_app: Any,
        flow_to_component_map: dict[str, dict[str, str]] | None = None,
        entity_action_flows: dict[str, str] | None = None,
        component_order: Sequence[str] | None = None,
        call_to_action_str: str = "",
        activity_transition_rates: dict[str, Any] | None = None,
        action_mode: str = "custom",
        enable_tool_calling: bool = False,
    ):
        """Initialize MultiFlowSMAct.

        Args:
            flow_to_component_map: Maps flow_name to {role: component_key}.
                                  Example: {"active": {"observe": "observe__timeline_make_observation"}}
            entity_action_flows: Maps entity name to flow name.
        """
        super().__init__(
            model=model,
            entity_names=entity_names,
            sm_app=sm_app,
            entity_action_flows=entity_action_flows,
            component_order=component_order,
            call_to_action_str=call_to_action_str,
            activity_transition_rates=activity_transition_rates,
            action_mode=action_mode,
            enable_tool_calling=enable_tool_calling,
        )
        self.flow_to_component_map = dict(flow_to_component_map or {})

    def _extract_active_entity_name(self, action_spec: entity_lib.ActionSpec) -> str | None:
        """Extract the active entity name from observation/resolve action specs."""
        if action_spec.output_type == entity_lib.OutputType.MAKE_OBSERVATION:
            call_to_action = str(action_spec.call_to_action or "").strip()
            match = re.search(r"What is the current situation faced by (.+?)\?", call_to_action)
            if match:
                return match.group(1).strip()
            return call_to_action or None

        if action_spec.output_type == entity_lib.OutputType.RESOLVE:
            call_to_action = str(action_spec.call_to_action or "")
            if ":" not in call_to_action:
                return None
            return call_to_action.split(":", 1)[0].strip() or None

        return None

    def _component_key_for_role(
        self,
        *,
        entity_name: str | None,
        role: str,
    ) -> str | None:
        flow_name = self.get_flow_for_entity(entity_name or "")
        component_map = self.flow_to_component_map.get(flow_name)
        if component_map is None:
            component_map = self.flow_to_component_map.get("default")
        if component_map is None:
            return None
        component_key = component_map.get(role)
        if component_key is None:
            return None
        return str(component_key).strip() or None

    def get_flow_for_entity(self, entity_name: str) -> str:
        """Get the action flow assigned to an entity.

        Args:
            entity_name: Name of the entity

        Returns
        -------
            Flow name (e.g., "active", "fixed_pre", defaults to "default")
        """
        return self.entity_action_flows.get(entity_name, "default")

    @override
    def _make_observation(  # type: ignore[misc]
        self,
        contexts: entity_component.ComponentContextMapping,
        action_spec: entity_lib.ActionSpec,
    ) -> str:
        entity_name = self._extract_active_entity_name(action_spec)
        selected_key = self._component_key_for_role(entity_name=entity_name, role="observe")
        if selected_key and selected_key in contexts:
            result = str(contexts[selected_key])
            self._log(result, result, action_spec)
            return result
        return super()._make_observation(contexts, action_spec)

    @override
    def _resolve(  # type: ignore[misc]
        self,
        contexts: entity_component.ComponentContextMapping,
        action_spec: entity_lib.ActionSpec,
    ) -> str:
        entity_name = self._extract_active_entity_name(action_spec)
        selected_key = self._component_key_for_role(entity_name=entity_name, role="resolve")
        if selected_key and selected_key in contexts:
            result = str(contexts[selected_key])
            self._log(result, result, action_spec)
            return result
        return super()._resolve(contexts, action_spec)
