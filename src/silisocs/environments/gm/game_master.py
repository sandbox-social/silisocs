"""Component-based native game masters."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from silisocs.environments.backends.factory import create_backend_app
from silisocs.environments.gm.base_game_master import BaseGameMaster
from silisocs.environments.gm.components.base import (
    ActionPromptComponent,
    InitializeComponent,
    NextActingComponent,
    ObservationComponent,
    ResolveComponent,
    UpdateComponent,
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
from silisocs.environments.gm.context import GameMasterContext
from silisocs.runtime.io import EventLogger
from silisocs.runtime.prompts.action_prompts import PromptAdditions, compile_action_prompt
from silisocs.runtime.types import ActionOutput, ActionSpec

_LOGGER = logging.getLogger(__name__)

_SLOTS = ("initialize", "next_acting", "action_prompt", "observe", "resolve", "update")
_ACTION_MODE_TO_RESOLVE = {"custom": "parsed_action", "generic": "generic_action"}


def build_generic_action_prompt(
    *,
    backend: Any,
    tool_calling_mode: str,
    prompt_config: Mapping[str, Any] | None = None,
    output_style: str = "",
    add_action_count_guidance: bool = True,
) -> str:
    """Build a generic action prompt from a backend action catalog."""
    prompt_cfg = dict(prompt_config or {})
    resolved_output_style = str(prompt_cfg.get("output_style", "") or "").strip()
    if not resolved_output_style:
        resolved_output_style = str(output_style or "")
    return compile_action_prompt(
        base_prompt=str(backend.generate_generic_action_prompt() or "").strip(),
        output_style=resolved_output_style,
        tool_calling_mode=tool_calling_mode,
        additions=PromptAdditions(add_action_count_guidance=add_action_count_guidance),
    )


@dataclass(frozen=True)
class GameMasterComponentSlots:
    """Typed component slots owned by one game master."""

    initialize: InitializeComponent
    next_acting: NextActingComponent
    action_prompt: ActionPromptComponent
    observe: ObservationComponent
    resolve: ResolveComponent
    update: UpdateComponent


class ComponentGameMaster(BaseGameMaster):
    """Default native game master backed by slottable components."""

    def __init__(
        self,
        *,
        name: str,
        backend_config: Mapping[str, Any] | None = None,
        components: Mapping[str, Any] | None = None,
        backend: Any | None = None,
        backend_type: str | None = None,
        component_slots: GameMasterComponentSlots | None = None,
        model: Any | None = None,
        agents: Sequence[Any] = (),
        action_prompt_template: str = "",
        action_mode: str = "custom",
        tool_calling_mode: str = "none",
        sim_roles: Mapping[str, str] | None = None,
        agent_flow_tags: Mapping[str, str] | None = None,
        owned_flows: Sequence[str] = (),
        prompt_config: Mapping[str, Any] | None = None,
        sequence: int = 0,
        mode: str = "shared",
    ) -> None:
        self._name = str(name)
        self.model = model
        self.agents = tuple(agents)
        self.sim_roles = dict(sim_roles or {})
        self.agent_flow_tags = dict(agent_flow_tags or {})
        self.owned_flows = tuple(str(flow) for flow in owned_flows)
        self.sequence = int(sequence)
        self.mode = str(mode)
        if backend is None:
            if backend_config is None:
                raise ValueError("ComponentGameMaster requires backend_config or backend.")
            self.backend_type = _backend_type(backend_config)
            self.backend = _create_backend(backend_config, gm_name=self._name)
        else:
            self.backend = backend
            self.backend_type = str(backend_type or getattr(backend, "backend_type", "") or "")
            if not self.backend_type:
                raise ValueError(
                    "ComponentGameMaster requires backend_type when backend is provided."
                )
        self.action_prompt_template = _resolve_action_prompt_template(
            backend=self.backend,
            action_mode=action_mode,
            action_prompt_template=action_prompt_template,
            tool_calling_mode=tool_calling_mode,
            prompt_config=prompt_config,
        )
        self.action_output_mode = _action_output_mode(components or {}, action_mode)
        self.enable_tool_calling = str(tool_calling_mode).strip().lower() in {"single", "multi"}
        self.context = GameMasterContext(
            gm_name=self.name,
            backend=self.backend,
            agents=self.agents,
            agent_names=tuple(agent.name for agent in self.agents),
            agent_flow_tags=self.agent_flow_tags,
            model=self.model,
            event_logger=getattr(self.backend, "action_logger", None),
        )
        if component_slots is None:
            slots, registry, flow_map = self._build_components(
                components=dict(components or {}),
                tool_calling_mode=str(tool_calling_mode or "none").strip().lower(),
            )
        else:
            slots = component_slots
            registry = {
                "initialize": slots.initialize,
                "next_acting": slots.next_acting,
                "action_prompt": slots.action_prompt,
                "observe": slots.observe,
                "resolve": slots.resolve,
                "update": slots.update,
            }
            flow_map = {}
        self.initialize_component = slots.initialize
        self.next_acting = slots.next_acting
        self.action_prompt_component = slots.action_prompt
        self.observe_component = slots.observe
        self.resolve_component = slots.resolve
        self.update_component = slots.update
        self._component_registry = registry
        self.flow_to_component_map = flow_map
        self._initialized = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def components(self) -> Mapping[str, Any]:
        return dict(self._component_registry)

    def get_component(self, key: str, type_: Any | None = None) -> Any:
        component = self._component_registry[key]
        if type_ is not None and not isinstance(component, type_):
            raise TypeError(f"Component {key!r} is not {type_!r}.")
        return component

    def initialize(self, *, agents: Sequence[Any], context: Any) -> None:
        self._refresh_context(agents)
        for component in self._components_for_role(role="initialize"):
            component.initialize(agents=agents, gm_context=self.context, context=context)
        self._initialized = True

    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        self._refresh_context(agents)
        for component in self._components_for_role(role="update"):
            component.update(step=step, agents=agents, context=context)

    def acting_agents(self, candidate_agents: Sequence[Any]) -> list[str]:
        by_name = {agent.name: agent for agent in candidate_agents}
        component = cast(
            NextActingComponent,
            self._component_for_candidate_role(
                candidate_agents=candidate_agents,
                role="next_acting",
            ),
        )
        names: list[str] = []
        known = set(self.agent_flow_tags)
        for raw_name in component.acting_agent_names():
            name = str(raw_name).strip()
            if not name:
                continue
            if name not in by_name:
                # A known agent outside this batch's candidates was excluded by
                # scheduling (sim-level participation filter or flow subsetting) —
                # expected, skip silently. A name the GM has never heard of is a
                # config error worth flagging.
                if name not in known:
                    _LOGGER.warning(
                        "Ignoring unknown acting agent '%s' from game master '%s'.",
                        name,
                        self.name,
                    )
                continue
            names.append(name)
        return names

    def action_prompt(self, agent_name: str) -> ActionSpec:
        component = cast(
            ActionPromptComponent,
            self._component_for_agent_role(agent_name=agent_name, role="action_prompt"),
        )
        return component.action_prompt(agent_name)

    def make_observation(self, agent_name: str) -> str:
        component = cast(
            ObservationComponent,
            self._component_for_agent_role(agent_name=agent_name, role="observe"),
        )
        return str(component.make_observation(agent_name))

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        component = cast(
            ResolveComponent,
            self._component_for_agent_role(agent_name=agent_name, role="resolve"),
        )
        return str(component.resolve_action(agent_name, action))

    def get_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "initialized": self._initialized,
            "components": {
                key: component.get_state()
                for key, component in self._component_registry.items()
                if callable(getattr(component, "get_state", None))
            },
            # agent->flow tags are re-materialized from config on resume, not from
            # the checkpoint. Record a fingerprint so a config change that would
            # silently mis-route checkpoint replay (or diverge the second half of a
            # run) is detected on restore. See set_state's validation below. The
            # flow->GM routing topology (flow_chains) is engine config, not GM state.
            "scheduling": {
                "agent_flow_tags": dict(self.agent_flow_tags),
                "owned_flows": list(self.owned_flows),
            },
        }
        # Use the explicit capability signal, not dict-truthiness: a backend that
        # legitimately has empty state this step must still be marked authoritative,
        # while a non-snapshotable backend (e.g. a live external server with
        # perform_operations) must be left out so the restore path falls through to
        # the configured restore strategy.
        if getattr(self.backend, "provides_checkpoint_state", False):
            state["backend"] = {
                "backend_type": self.backend_type,
                "state": self.backend.get_state(),
            }
        return state

    def set_state(self, state: Mapping[str, Any]) -> None:
        data = dict(state or {})
        self._initialized = bool(data.get("initialized", self._initialized))
        backend_payload = data.get("backend")
        if backend_payload is not None:
            if not isinstance(backend_payload, Mapping):
                raise TypeError("Game master checkpoint backend state must be a mapping.")
            backend_type = str(backend_payload.get("backend_type") or "")
            if backend_type and backend_type != self.backend_type:
                raise ValueError(
                    f"Checkpoint backend type {backend_type!r} does not match "
                    f"game master backend type {self.backend_type!r}."
                )
            backend_state = backend_payload.get("state")
            if not isinstance(backend_state, Mapping):
                raise TypeError("Game master checkpoint backend.state must be a mapping.")
            self.backend.set_state(dict(backend_state))
        scheduling = data.get("scheduling")
        if isinstance(scheduling, Mapping):
            self._warn_on_scheduling_drift(scheduling)
        component_states = data.get("components", {})
        if not isinstance(component_states, Mapping):
            return
        for key, value in component_states.items():
            component = self._component_registry.get(str(key))
            if component is not None:
                component.set_state(value)

    def _warn_on_scheduling_drift(self, checkpointed: Mapping[str, Any]) -> None:
        """Warn loudly if resume-time agent->flow tags diverge from the checkpoint.

        The agent->flow mapping (this GM's component-routing surface) is rebuilt
        from the live config, not restored from the checkpoint. If a tag changed,
        checkpoint replay routes historical events by the *new* tag, which can
        mis-route or raise. We warn rather than hard-fail because some changes
        (e.g. adding agents) are legitimate, but the divergence must be visible.
        We compare only agents present in BOTH maps, so a legitimate roster change
        (an agent added or removed) does not warn — only a changed existing tag.
        (The flow->GM routing topology is engine config, owned by the step strategy
        and the restore router; it is not checkpointed here.)
        """
        saved_tags = dict(checkpointed.get("agent_flow_tags") or {})
        live_tags = {str(k): str(v) for k, v in self.agent_flow_tags.items()}
        changed_tags = {
            agent: (saved_tags.get(agent), live_tags.get(agent))
            for agent in set(saved_tags) & set(live_tags)
            if str(saved_tags.get(agent)) != str(live_tags.get(agent))
        }
        if changed_tags:
            _LOGGER.warning(
                "Game master %r resumed with agent->flow tags that differ from the "
                "checkpoint (changed: %s). Checkpoint replay routes historical events "
                "by the current config, so this can mis-route actions. Resume with the "
                "original flow configuration unless the change is intentional.",
                self.name,
                changed_tags,
            )

    def _build_components(
        self,
        *,
        components: Mapping[str, Any],
        tool_calling_mode: str,
    ) -> tuple[GameMasterComponentSlots, dict[str, Any], dict[str, dict[str, str]]]:
        _require_component_slot(components, "initialize")
        initialize = build_initialize_component(components.get("initialize"), context=self.context)
        next_acting = build_next_acting_component(
            components.get("next_acting"),
            context=self.context,
            sim_roles=self.sim_roles,
        )
        action_prompt = build_action_prompt_component(
            components.get("action_prompt"),
            context=self.context,
            action_prompt_template=self.action_prompt_template,
            enable_tool_calling=self.enable_tool_calling,
            tool_calling_mode=tool_calling_mode,
        )
        observe = build_observe_component(components.get("observe"), context=self.context)
        resolve = build_resolve_component(
            components.get("resolve") or {"built_in": self.action_output_mode},
            context=self.context,
            action_prompt_template=self.action_prompt_template,
        )
        update = build_update_component(
            components.get("update"),
            context=self.context,
            backend_type=self.backend_type,
        )
        registry = {
            "initialize": initialize,
            "next_acting": next_acting,
            "action_prompt": action_prompt,
            "observe": observe,
            "resolve": resolve,
            "update": update,
        }
        _validate_update_component(update)
        return (
            GameMasterComponentSlots(
                initialize, next_acting, action_prompt, observe, resolve, update
            ),
            registry,
            {},
        )

    def _refresh_context(self, agents: Sequence[Any]) -> None:
        self.agents = tuple(agents)
        self.context = GameMasterContext(
            gm_name=self.name,
            backend=self.backend,
            agents=self.agents,
            agent_names=tuple(agent.name for agent in self.agents),
            agent_flow_tags=self.agent_flow_tags,
            model=self.model,
            event_logger=getattr(self.backend, "action_logger", None),
        )

    def _flow_for_agent(self, agent_name: str) -> str:
        return str(self.agent_flow_tags.get(agent_name, "default") or "default")

    def _flow_for_candidates(self, candidate_agents: Sequence[Any]) -> str:
        flows = {
            self._flow_for_agent(str(getattr(agent, "name", "") or ""))
            for agent in candidate_agents
            if str(getattr(agent, "name", "") or "").strip()
        }
        return next(iter(flows)) if len(flows) == 1 else "default"

    def _component_for_agent_role(self, *, agent_name: str, role: str) -> Any:
        flow = self._flow_for_agent(agent_name)
        key = _component_key(self.flow_to_component_map, flow=flow, role=role)
        return self._component_registry[key]

    def _component_for_candidate_role(self, *, candidate_agents: Sequence[Any], role: str) -> Any:
        flow = self._flow_for_candidates(candidate_agents)
        key = _component_key(self.flow_to_component_map, flow=flow, role=role)
        return self._component_registry[key]

    def _components_for_role(self, *, role: str) -> list[Any]:
        if not self.flow_to_component_map:
            return [self._component_registry[role]]
        keys = [
            role_map[role] for role_map in self.flow_to_component_map.values() if role in role_map
        ]
        if not keys:
            keys = [role]
        seen: set[str] = set()
        result: list[Any] = []
        for key in keys:
            if key in seen:
                continue
            if key not in self._component_registry:
                raise KeyError(f"Unknown {role} component key {key!r}.")
            seen.add(key)
            result.append(self._component_registry[key])
        return result


class MultiFlowGameMaster(ComponentGameMaster):
    """Native game master that routes component slots by agent flow."""

    def _build_components(
        self,
        *,
        components: Mapping[str, Any],
        tool_calling_mode: str,
    ) -> tuple[GameMasterComponentSlots, dict[str, Any], dict[str, dict[str, str]]]:
        _require_component_slot(components, "initialize")
        built: dict[str, dict[str, Any]] = {
            "initialize": _single_and_instances(
                "initialize",
                build_initialize_component(components.get("initialize"), context=self.context),
                build_initialize_components(components.get("initialize"), context=self.context),
            ),
            "next_acting": _single_and_instances(
                "next_acting",
                build_next_acting_component(
                    components.get("next_acting"),
                    context=self.context,
                    sim_roles=self.sim_roles,
                ),
                build_next_acting_components(
                    components.get("next_acting"),
                    context=self.context,
                    sim_roles=self.sim_roles,
                ),
            ),
            "action_prompt": _single_and_instances(
                "action_prompt",
                build_action_prompt_component(
                    components.get("action_prompt"),
                    context=self.context,
                    action_prompt_template=self.action_prompt_template,
                    enable_tool_calling=self.enable_tool_calling,
                    tool_calling_mode=tool_calling_mode,
                ),
                build_action_prompt_components(
                    components.get("action_prompt"),
                    context=self.context,
                    action_prompt_template=self.action_prompt_template,
                    enable_tool_calling=self.enable_tool_calling,
                    tool_calling_mode=tool_calling_mode,
                ),
            ),
            "observe": _single_and_instances(
                "observe",
                build_observe_component(components.get("observe"), context=self.context),
                build_observe_components(components.get("observe"), context=self.context),
            ),
            "resolve": _single_and_instances(
                "resolve",
                build_resolve_component(
                    components.get("resolve") or {"built_in": self.action_output_mode},
                    context=self.context,
                    action_prompt_template=self.action_prompt_template,
                ),
                build_resolve_components(
                    components.get("resolve") or {"built_in": self.action_output_mode},
                    context=self.context,
                    action_prompt_template=self.action_prompt_template,
                ),
            ),
            "update": _single_and_instances(
                "update",
                build_update_component(
                    components.get("update"),
                    context=self.context,
                    backend_type=self.backend_type,
                ),
                build_update_components(
                    components.get("update"),
                    context=self.context,
                    backend_type=self.backend_type,
                ),
            ),
        }
        registry: dict[str, Any] = {}
        for role_components in built.values():
            registry.update(role_components)
        for component in built["update"].values():
            _validate_update_component(component)
        flow_map = _build_flow_to_component_map(self.agent_flow_tags, built, components)
        default = flow_map["default"]
        slots = GameMasterComponentSlots(
            cast(InitializeComponent, registry[default["initialize"]]),
            cast(NextActingComponent, registry[default["next_acting"]]),
            cast(ActionPromptComponent, registry[default["action_prompt"]]),
            cast(ObservationComponent, registry[default["observe"]]),
            cast(ResolveComponent, registry[default["resolve"]]),
            cast(UpdateComponent, registry[default["update"]]),
        )
        return slots, registry, flow_map


def _backend_type(backend_config: Mapping[str, Any]) -> str:
    backend_type = str(dict(backend_config or {}).get("backend_type") or "").strip()
    if not backend_type:
        raise ValueError("backend_config.backend_type is required.")
    return backend_type


def _create_backend(backend_config: Mapping[str, Any], *, gm_name: str) -> Any:
    cfg = dict(backend_config or {})
    backend_type = _backend_type(cfg)
    output_rootname = str(cfg.get("output_rootname") or "")
    action_logger = EventLogger(
        "action",
        os.path.join(output_rootname, "action_events.jsonl"),
        static_fields={"gm_name": gm_name, "backend_type": backend_type},
    )
    action_logger.episode_idx = 0
    backend = create_backend_app(
        backend_type=backend_type,
        action_logger=action_logger,
        perform_operations=bool(cfg.get("perform_operations", False)),
        app_description=str(cfg.get("app_description") or ""),
        db_path=os.path.join(output_rootname, f"{backend_type}.db"),
        class_path=cfg.get("class_path"),
        params=dict(cfg.get("params") or {}),
    )
    # Apply config-driven agent-facing action renames/aliases before filters so
    # enabled/excluded lists may reference either canonical or aliased names.
    action_aliases = cfg.get("action_aliases")
    if action_aliases and hasattr(backend, "set_action_aliases"):
        backend.set_action_aliases(action_aliases)
    enabled_actions = cfg.get("enabled_actions")
    excluded_actions = cfg.get("excluded_actions")
    excluded = (
        [str(action).strip() for action in excluded_actions]
        if isinstance(excluded_actions, Sequence) and not isinstance(excluded_actions, (str, bytes))
        else [str(excluded_actions).strip()]
        if excluded_actions is not None
        else None
    )
    if (
        str(cfg.get("turn_policy_built_in") or "").strip() == "open_ended"
        and excluded
        and any(name.upper() == "FINISHED" for name in excluded)
    ):
        raise ValueError("open_ended turn policy requires FINISHED; do not exclude it.")
    if enabled_actions is not None:
        actions = (
            [str(action).strip() for action in enabled_actions]
            if isinstance(enabled_actions, Sequence)
            and not isinstance(enabled_actions, (str, bytes))
            else [str(enabled_actions).strip()]
        )
        if str(cfg.get("turn_policy_built_in") or "").strip() == "open_ended":
            names = {name.upper() for name in actions if name}
            if "FINISHED" not in names:
                actions.append("FINISHED")
        backend.set_action_filters(enabled_actions=actions, excluded_actions=excluded)
    elif excluded is not None:
        backend.set_action_filters(enabled_actions=None, excluded_actions=excluded)
    return backend


def _resolve_action_prompt_template(
    *,
    backend: Any,
    action_mode: str,
    action_prompt_template: str,
    tool_calling_mode: str,
    prompt_config: Mapping[str, Any] | None,
) -> str:
    if str(action_mode or "custom").strip().lower() != "generic":
        return str(action_prompt_template or "")
    prompt_cfg = dict(prompt_config or {})
    return build_generic_action_prompt(
        backend=backend,
        tool_calling_mode=tool_calling_mode,
        prompt_config=prompt_cfg,
        add_action_count_guidance=bool(prompt_cfg.get("add_action_count_guidance", True)),
    )


def _action_output_mode(components: Mapping[str, Any], action_mode: str) -> str:
    resolve_slot = dict(dict(components or {}).get("resolve") or {})
    built_in = resolve_slot.get("built_in")
    if built_in:
        return str(built_in)
    return _ACTION_MODE_TO_RESOLVE.get(
        str(action_mode or "custom").strip().lower(), "parsed_action"
    )


def _require_component_slot(components: Mapping[str, Any], slot: str) -> None:
    if not dict(components or {}).get(slot):
        raise ValueError(f"Native game masters require env.gm.components.{slot}.")


def _validate_update_component(component: Any) -> None:
    validator = getattr(component, "validate_recsys_types", None)
    if callable(validator):
        validator()


def _single_and_instances(role: str, default: Any, instances: Mapping[str, Any]) -> dict[str, Any]:
    return {role: default, **dict(instances)}


def _component_key(
    flow_map: Mapping[str, Mapping[str, str]],
    *,
    flow: str,
    role: str,
) -> str:
    if not flow_map:
        return role
    role_map = flow_map.get(flow) or flow_map.get("default", {})
    return str(role_map.get(role, role) or role)


def _build_flow_to_component_map(
    agent_flow_tags: Mapping[str, str],
    components_by_role: Mapping[str, Mapping[str, Any]],
    slot_cfg_by_role: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    flow_maps = {
        role: _normalize_flow_map(dict(slot_cfg_by_role.get(role) or {}).get("flow_map"))
        for role in _SLOTS
    }
    flows = {str(flow or "default") for flow in agent_flow_tags.values()}
    for flow_map in flow_maps.values():
        flows.update(flow_map)
    flows.add("default")

    mapping: dict[str, dict[str, str]] = {}
    for flow in flows:
        mapping[flow] = {}
        for role in _SLOTS:
            available = set(components_by_role[role])
            default_key = role if role in available else next(iter(available))
            requested = flow_maps[role].get(flow) or flow_maps[role].get("default") or default_key
            mapping[flow][role] = _resolve_component_key(
                role=role,
                requested_key=requested,
                available_keys=available,
                default_key=default_key,
                flow=flow,
            )
    return mapping


def _normalize_flow_map(raw_flow_map: Any) -> dict[str, str]:
    if not isinstance(raw_flow_map, Mapping):
        return {}
    return {
        str(flow).strip(): str(component_key).strip()
        for flow, component_key in raw_flow_map.items()
        if str(flow).strip() and str(component_key).strip()
    }


def _resolve_component_key(
    *,
    role: str,
    requested_key: str,
    available_keys: set[str],
    default_key: str,
    flow: str,
) -> str:
    candidate = str(requested_key or "").strip()
    if not candidate:
        return default_key
    candidates = [candidate]
    prefix = f"{role}__"
    if not candidate.startswith(prefix):
        candidates.append(f"{prefix}{candidate}")
        candidates.append(f"{prefix}{re.sub(r'(?<!^)(?=[A-Z])', '_', candidate).lower()}")
    for item in candidates:
        if item in available_keys:
            return item
    raise ValueError(
        f"Invalid {role} flow_map for flow '{flow}': requested '{candidate}', "
        f"available keys are {sorted(available_keys)}."
    )
