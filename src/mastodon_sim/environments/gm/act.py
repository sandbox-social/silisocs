"""Custom social-media SwitchAct components.

This module provides two SwitchAct variants:
1. SMAct: Simple, single-component-per-role (default)
2. MultiFlowSMAct: Multi-flow aware, routes entities to flow-specific components

Observe/resolve/next-acting are delegated to Concordia-native context
components configured via YAML and routed by these act components.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from concordia.components import game_master as gm_components  # type: ignore[attr-defined]
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from typing_extensions import override

DEFAULT_SESSION_TERMINATE_STR = "The Social-Media session has been completed."


class SMAct(gm_components.switch_act.SwitchAct):
    """SwitchAct specialization for social-media prompts and GM transitions (simple mode).

    Used when enable_multi_flow=false. All entities use same component instances.
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
            base_prompt = (
                "Conduct a social-media action. Determine what ONE action would be most appropriate."
            )

        # Step 2: If tool-calling is disabled, return prompt as-is
        if not self.enable_tool_calling:
            return f"prompt: {base_prompt} ;;type: free"

        # Step 3: If tool-calling is enabled, add tool schemas
        call_to_action = (
            "### TOOL_CALLING_MODE ###\n"
            f"{base_prompt}"
        )
        return f"prompt: {call_to_action} ;;type: free"


class MultiFlowSMAct(SMAct):
    """SwitchAct with multi-flow component routing.

    Used when enable_multi_flow=true. Routes entities to flow-specific component
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
        self._entity_components_cache: dict[str, set[str]] = {}

    def get_components_for_entity(self, entity_name: str) -> set[str]:
        """Get component keys that a specific entity should use based on its flow.

        Uses cache to avoid repeated lookups.

        Args:
            entity_name: Name of the entity

        Returns:
            Set of component keys (e.g., {"observe__timeline_make_observation", "resolve__parsed_action"})
        """
        if entity_name in self._entity_components_cache:
            return self._entity_components_cache[entity_name]

        # Get entity's flow (default to "default" if not found)
        flow = self.entity_action_flows.get(entity_name, "default")

        # Get component mapping for this flow
        if flow not in self.flow_to_component_map:
            # If flow not in map, try "default"
            if "default" in self.flow_to_component_map:
                flow = "default"
            else:
                # No mapping found, return empty set (will use all components)
                self._entity_components_cache[entity_name] = set()
                return set()

        # Extract component keys from {role: component_key} dict
        component_dict = self.flow_to_component_map[flow]
        component_keys = set(component_dict.values())

        self._entity_components_cache[entity_name] = component_keys
        return component_keys

    def get_flow_for_entity(self, entity_name: str) -> str:
        """Get the action flow assigned to an entity.

        Args:
            entity_name: Name of the entity

        Returns:
            Flow name (e.g., "active", "fixed_pre", defaults to "default")
        """
        return self.entity_action_flows.get(entity_name, "default")
