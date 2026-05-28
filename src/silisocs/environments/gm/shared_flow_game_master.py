"""Multi-flow environment game master with component routing.

Use this GM class when one game master should route component slots by agent flow. Supports:
- Multiple component instances per role (e.g., TimelineObservation + EpisodeObservation)
- Flow-based component routing (agents use components based on their assigned flow)
- Per-flow multi-field configuration (different algorithms/strategies per flow)

All agents share one backend/app state but may see different observations/resolutions.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.environments.gm.base_game_master import (
    EnvironmentGameMaster,
    GameMasterComponentSlots,
    _GameMasterWiring,
)
from silisocs.environments.gm.components.factory import (
    build_action_prompt_component,
    build_action_prompt_components,
    build_initialize_component,
    build_initialize_components,
    build_next_acting_component,
    build_next_acting_components,
    build_observe_component,
    build_observe_components,
    build_resolve_component,
    build_resolve_components,
    build_update_component,
    build_update_components,
)

logger = logging.getLogger(__name__)


class _FlowRoutedGameMasterWiring(_GameMasterWiring):
    """Multi-flow game master with explicit component routing per flow.

    This GM enables advanced scenarios where different agents (grouped by flow)
    use different component implementations and configurations.

    Configuration:
        env:
          gm:
            class_path: silisocs.environments.gm.shared_flow_game_master.FlowRoutedGameMaster

    Component routing is built from agent flow tags and the multi-component
    configuration in env.gm.components.<slot>.
    """

    def _is_shared_flow_mode(self) -> bool:
        """Indicates this is a multi-flow aware mode."""
        return True

    def build_runtime_kwargs(
        self,
        model: Any | None = None,
    ) -> dict[str, Any]:
        """Build multi-flow GM runtime keyword arguments.

        Overrides base build to handle multiple component instances per role.
        """
        model = model or self.model
        name = str(self.params.get("name"))
        environment_data = dict(self.params["environment_data"])
        action_prompt_template = str(self.params.get("action_prompt_template") or "")

        agent_names = [agent.name for agent in self.agents]

        action_mode_to_resolve_map = {
            "custom": "parsed_action",
            "generic": "generic_action",
        }
        gm_components_cfg = self._resolve_gm_components_cfg(self.params)

        sim_roles = dict(environment_data.get("sim_roles", {}) or {})
        agent_flow_tags = dict(environment_data.get("agent_flow_tags", {}) or {})
        gm_orchestration = dict(environment_data.get("gm_orchestration", {}) or {})
        gm_prompt_cfg = dict(gm_orchestration.get("prompt", {}) or {})
        initializer_cfg = dict(gm_components_cfg.get("initialize") or {})
        if not initializer_cfg:
            raise ValueError("FlowRoutedGameMaster requires `env.gm.components.initialize`.")

        next_acting_slot = dict(gm_components_cfg.get("next_acting", {}))

        resolve_slot = dict(gm_components_cfg.get("resolve", {}))
        if not resolve_slot:
            resolve_slot = {
                "built_in": action_mode_to_resolve_map.get(
                    str(self.params.get("action_mode") or "custom"), "parsed_action"
                ),
            }
        tool_calling_mode = str(self.params.get("tool_calling_mode") or "none").strip().lower()
        enable_tool_calling = tool_calling_mode in {"single", "multi"}

        backend_cfg = dict(self.params.get("backend_config") or {})
        backend_type = str(backend_cfg.get("backend_type") or "").strip()
        if not backend_type:
            raise ValueError("FlowRoutedGameMaster backend_config.backend_type is required.")
        backend = self._create_backend_app()

        action_mode = str(self.params.get("action_mode") or "custom").strip().lower()
        if action_mode == "generic":
            action_prompt_template = self.build_generic_prompt(
                backend=backend,
                tool_calling_mode=tool_calling_mode,
                gm_prompt_cfg=gm_prompt_cfg,
            )

        action_output_mode = str(resolve_slot.get("built_in", "parsed_action") or "parsed_action")

        initialize_component = build_initialize_component(initializer_cfg)
        initialize_components = {"initialize": initialize_component}
        initialize_components.update(build_initialize_components(initializer_cfg))

        next_actor = build_next_acting_component(
            next_acting_slot,
            agent_names=agent_names,
            sim_roles=sim_roles,
        )
        next_acting_components = {"next_acting": next_actor}
        next_acting_components.update(
            build_next_acting_components(
                next_acting_slot,
                agent_names=agent_names,
                sim_roles=sim_roles,
            )
        )

        action_prompt_slot = dict(gm_components_cfg.get("action_prompt", {}))
        action_prompt_component = build_action_prompt_component(
            action_prompt_slot,
            backend=backend,
            action_prompt_template=action_prompt_template,
            enable_tool_calling=enable_tool_calling,
            tool_calling_mode=tool_calling_mode,
        )
        action_prompt_components = {"action_prompt": action_prompt_component}
        action_prompt_components.update(
            build_action_prompt_components(
                action_prompt_slot,
                backend=backend,
                action_prompt_template=action_prompt_template,
                enable_tool_calling=enable_tool_calling,
                tool_calling_mode=tool_calling_mode,
            )
        )

        # Build observe components.
        observe_slots = dict(gm_components_cfg.get("observe", {}))
        episode_observation_flow = (
            dict(observe_slots.get("params") or {}).get("episode_observation_flow", "fixed_pre")
            if isinstance(observe_slots, dict) and "params" in observe_slots
            else "fixed_pre"
        )

        # Check if observe has nested structure (multi-instance) or flat structure (single)
        has_multi_instances = isinstance(observe_slots.get("instances"), dict) or any(
            isinstance(v, dict) and ("built_in" in v or "class_path" in v)
            for v in observe_slots.values()
            if v  # Skip None/empty values
        )

        if has_multi_instances:
            # Multi-instance mode: build multiple components
            logger.info("Building multiple observe components for multi-flow mode")
            observe_components = build_observe_components(
                observe_slots,
                model=model,
                agent_names=agent_names,
                backend=backend,
                agent_flow_tags=agent_flow_tags,
                episode_observation_flow=episode_observation_flow,
            )
        else:
            # Single-instance mode: use single component (fallback)
            logger.info("Using single observe component (no multi-instance config)")
            single_observe = build_observe_component(
                observe_slots,
                model=model,
                agent_names=agent_names,
                backend=backend,
                agent_flow_tags=agent_flow_tags,
                episode_observation_flow=episode_observation_flow,
            )
            class_name = single_observe.__class__.__name__
            kebab_key = _class_to_kebab_case(class_name)
            full_key = f"observe__{kebab_key}"
            observe_components = {full_key: single_observe}

        resolve_component = build_resolve_component(
            resolve_slot,
            backend=backend,
            model=model,
            action_prompt_template=action_prompt_template,
            agents_by_name={agent.name: agent for agent in self.agents},
        )
        resolve_components = {"resolve": resolve_component}
        resolve_components.update(
            build_resolve_components(
                resolve_slot,
                backend=backend,
                model=model,
                action_prompt_template=action_prompt_template,
                agents_by_name={agent.name: agent for agent in self.agents},
            )
        )

        update_slot = dict(gm_components_cfg.get("update", {}))
        update_component = build_update_component(
            update_slot,
            backend=backend,
            backend_type=backend_type,
        )
        update_components = {"update": update_component}
        update_components.update(
            build_update_components(
                update_slot,
                backend=backend,
                backend_type=backend_type,
            )
        )

        component_registry = {
            **initialize_components,
            **next_acting_components,
            **action_prompt_components,
            **resolve_components,
            **update_components,
        }
        component_registry.update(observe_components)

        for component in update_components.values():
            if hasattr(component, "validate_recsys_types") and callable(
                component.validate_recsys_types
            ):
                component.validate_recsys_types()

        flow_to_component_map = _build_flow_to_component_map(
            agent_flow_tags,
            {
                "initialize": initialize_components,
                "next_acting": next_acting_components,
                "action_prompt": action_prompt_components,
                "observe": observe_components,
                "resolve": resolve_components,
                "update": update_components,
            },
            {
                "initialize": initializer_cfg,
                "next_acting": next_acting_slot,
                "action_prompt": action_prompt_slot,
                "observe": observe_slots,
                "resolve": resolve_slot,
                "update": update_slot,
            },
        )

        logger.info(f"Built flow-to-component mapping: {flow_to_component_map}")
        component_slots = GameMasterComponentSlots(
            initialize=initialize_component,
            next_acting=next_actor,
            action_prompt=action_prompt_component,
            observe=observe_components[flow_to_component_map["default"]["observe"]],
            resolve=resolve_component,
            update=update_component,
        )

        return {
            "name": name,
            "model": model,
            "backend": backend,
            "backend_type": backend_type,
            "component_slots": component_slots,
            "component_registry": component_registry,
            "environment_data": environment_data,
            "action_prompt_template": action_prompt_template,
            "action_output_mode": action_output_mode,
            "activity_transition_rates": dict(
                dict(next_acting_slot.get("params", {}) or {}).get(
                    "activity_transition_rates",
                    {},
                )
                or {}
            ),
            "agent_flow_tags": agent_flow_tags,
            "gm_orchestration": gm_orchestration,
            "flow_to_component_map": flow_to_component_map,
            "shared_flow_mode": self._is_shared_flow_mode(),
            "enable_tool_calling": enable_tool_calling,
        }

    def build(
        self,
        model: Any | None = None,
    ) -> EnvironmentGameMaster:
        """Build multi-flow GM with component routing."""
        return EnvironmentGameMaster(**self.build_runtime_kwargs(model=model))


class FlowRoutedGameMaster(EnvironmentGameMaster):
    """Shared-flow native game master built directly from runtime config."""

    def __init__(
        self,
        *,
        model: Any | None = None,
        agents: Sequence[Any] = (),
        **params: Any,
    ) -> None:
        runtime_kwargs = _FlowRoutedGameMasterWiring(
            model=model,
            agents=agents,
            **params,
        ).build_runtime_kwargs(model=model)
        super().__init__(**runtime_kwargs)


def _class_to_kebab_case(class_name: str) -> str:
    """Convert ClassName to kebab-case (duplicate of factory utility)."""
    import re

    kebab = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name)
    return kebab.lower()


def _build_flow_to_component_map(
    agent_flow_tags: dict[str, str],
    components_by_role: Mapping[str, Mapping[str, Any]],
    slot_cfg_by_role: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, dict[str, str]]:
    """Build flow-to-component mapping from slot ``flow_map`` values."""
    mapping: dict[str, dict[str, str]] = {}
    flow_maps = {
        role: _normalize_flow_map(dict(slot_cfg or {}).get("flow_map"))
        for role, slot_cfg in slot_cfg_by_role.items()
    }

    unique_flows = set(agent_flow_tags.values())
    for flow_map in flow_maps.values():
        unique_flows.update(flow_map.keys())
    unique_flows.add("default")

    for flow in unique_flows:
        mapping[flow] = {}
        for role, components in components_by_role.items():
            available = set(components)
            default_key = role if role in available else next(iter(available), "")
            requested_key = flow_maps.get(role, {}).get(flow) or flow_maps.get(role, {}).get(
                "default"
            )
            mapping[flow][role] = _resolve_component_key(
                role=role,
                requested_key=requested_key or default_key,
                available_keys=available,
                default_key=default_key,
                flow=flow,
            )

    logger.debug(f"Flow-to-component mapping: {mapping}")
    return mapping


def _normalize_flow_map(raw_flow_map: Any) -> dict[str, str]:
    """Normalize flow_map config into {flow_tag: component_key}."""
    if not isinstance(raw_flow_map, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for flow_name, component_key in raw_flow_map.items():
        flow = str(flow_name).strip()
        key = str(component_key).strip()
        if flow and key:
            normalized[flow] = key
    return normalized


def _resolve_component_key(
    *,
    role: str,
    requested_key: str,
    available_keys: set[str],
    default_key: str,
    flow: str,
) -> str:
    """Resolve requested component key against available keys for one slot."""
    candidate = str(requested_key or "").strip()
    if not candidate:
        return default_key

    candidates = [candidate]
    prefix = f"{role}__"
    if not candidate.startswith(prefix):
        candidates.append(f"{prefix}{candidate}")
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", candidate).lower()
        candidates.append(f"{prefix}{snake}")

    for item in candidates:
        if item in available_keys:
            return item

    raise ValueError(
        f"Invalid {role} flow_map configuration for flow "
        f"'{flow}': requested '{candidate}', available keys are {sorted(available_keys)}."
    )
