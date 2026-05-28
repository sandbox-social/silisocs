"""Shared native game-master construction helpers."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from silisocs.environments.backends.base import BackendApp
    from silisocs.environments.gm.components.base import (
        ActionPromptComponent,
        InitializeComponent,
        NextActingComponent,
        ObservationComponent,
        ResolveComponent,
        UpdateComponent,
    )
    from silisocs.runtime.language_models.base import LanguageModel

from silisocs.environments.backends.factory import create_backend_app
from silisocs.environments.gm.components.factory import (
    build_action_prompt_component,
    build_initialize_component,
    build_next_acting_component,
    build_observe_component,
    build_resolve_component,
    build_update_component,
)
from silisocs.runtime.io import EventLogger
from silisocs.runtime.prompts.action_prompts import (
    PromptAdditions,
    compile_action_prompt,
)
from silisocs.runtime.types import ActionOutput, ActionSpec

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameMasterComponentSlots:
    """Typed native component slots owned by a game master."""

    initialize: InitializeComponent
    next_acting: NextActingComponent
    action_prompt: ActionPromptComponent
    observe: ObservationComponent
    resolve: ResolveComponent
    update: UpdateComponent


class BaseGameMaster(ABC):
    """Base native game-master surface shared by concrete GMs."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return this game master's stable runtime name."""

    @abstractmethod
    def initialize(self, *, agents: Sequence[Any], context: Any) -> None:
        """Initialize backend/environment state before the simulation loop."""

    @abstractmethod
    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        """Run one pre-turn update for this game master."""

    @abstractmethod
    def acting_agents(self, candidate_agents: Sequence[Any]) -> list[str]:
        """Return selected agent names for this turn."""

    @abstractmethod
    def action_prompt(self, agent_name: str) -> ActionSpec:
        """Return the action prompt for one agent."""

    @abstractmethod
    def make_observation(self, agent_name: str) -> str:
        """Build an observation for one agent."""

    @abstractmethod
    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        """Resolve one agent action through the backend."""


def build_generic_action_prompt(
    *,
    backend: Any,
    tool_calling_mode: str,
    gm_prompt_cfg: Mapping[str, Any] | None = None,
    output_style: str = "",
    add_action_count_guidance: bool = True,
) -> str:
    """Build a generic action prompt from a backend action catalog."""
    gm_prompt_cfg = dict(gm_prompt_cfg or {})
    resolved_output_style = str(gm_prompt_cfg.get("output_style", "") or "").strip()
    if not resolved_output_style:
        resolved_output_style = str(output_style or "")
    base_prompt = str(backend.generate_generic_action_prompt() or "").strip()
    return compile_action_prompt(
        base_prompt=base_prompt,
        output_style=resolved_output_style,
        tool_calling_mode=tool_calling_mode,
        additions=PromptAdditions(
            add_action_count_guidance=add_action_count_guidance,
        ),
    )


class EnvironmentGameMaster(BaseGameMaster):
    """Direct native game master coordinating one backend and component set."""

    def __init__(
        self,
        *,
        name: str,
        model: LanguageModel | None,
        backend: BackendApp,
        backend_type: str,
        component_slots: GameMasterComponentSlots,
        component_registry: Mapping[str, Any] | None = None,
        environment_data: Mapping[str, Any],
        action_prompt_template: str,
        action_output_mode: str,
        activity_transition_rates: Mapping[str, Any],
        agent_flow_tags: Mapping[str, str],
        gm_orchestration: Mapping[str, Any],
        flow_to_component_map: Mapping[str, Mapping[str, str]] | None = None,
        shared_flow_mode: bool = False,
        enable_tool_calling: bool = False,
    ) -> None:
        self._name = str(name)
        self.model = model
        self.backend = backend
        self.backend_type = str(backend_type)
        self.initialize_component = component_slots.initialize
        self.next_acting = component_slots.next_acting
        self.action_prompt_component = component_slots.action_prompt
        self.observe_component = component_slots.observe
        self.resolve_component = component_slots.resolve
        self.update_component = component_slots.update
        self._component_registry = {
            "initialize": self.initialize_component,
            "next_acting": self.next_acting,
            "action_prompt": self.action_prompt_component,
            "observe": self.observe_component,
            "resolve": self.resolve_component,
            "update": self.update_component,
            **dict(component_registry or {}),
        }
        self.environment_data = dict(environment_data or {})
        self.action_prompt_template = str(action_prompt_template or "")
        self.action_output_mode = str(action_output_mode or "parsed_action")
        self.activity_transition_rates = dict(activity_transition_rates or {})
        self.agent_flow_tags = dict(agent_flow_tags or {})
        self.gm_orchestration = dict(gm_orchestration or {})
        self.flow_to_component_map = {
            str(flow): {str(role): str(key) for role, key in mapping.items()}
            for flow, mapping in dict(flow_to_component_map or {}).items()
        }
        self.shared_flow_mode = bool(shared_flow_mode)
        self.enable_tool_calling = bool(enable_tool_calling)
        self._initialized = False

    @property
    def name(self) -> str:
        """Return this game master's name."""
        return self._name

    @property
    def components(self) -> Mapping[str, Any]:
        """Return configured GM components."""
        return dict(self._component_registry)

    def get_component(self, key: str, type_: Any | None = None) -> Any:
        """Return a configured component by key."""
        component = self._component_registry[key]
        if type_ is not None and not isinstance(component, type_):
            raise TypeError(f"Component {key!r} is not {type_!r}")
        return component

    def initialize(self, *, agents: Sequence[Any], context: Any) -> None:
        """Initialize this GM's backend/environment state before the simulation loop."""
        for component in self._components_for_role(role="initialize", default_key="initialize"):
            initializer = getattr(component, "initialize", None)
            if not callable(initializer):
                raise TypeError(
                    "Initialize component must expose initialize(agents, game_master, context)."
                )
            initializer(
                agents=agents,
                game_master=self,
                context=context,
            )
        self._initialized = True

    def _flow_for_agent(self, agent_name: str) -> str:
        return str(self.agent_flow_tags.get(agent_name, "default") or "default")

    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        """Run this GM's pre-turn update slot."""
        for component in self._components_for_role(role="update", default_key="update"):
            updater = getattr(component, "update", None)
            if not callable(updater):
                raise TypeError("Update component must expose update(step, agents, context).")
            updater(step=step, agents=agents, context=context)

    def _component_key_for_role(self, *, agent_name: str, role: str, default_key: str) -> str:
        flow = self._flow_for_agent(agent_name)
        component_map = self.flow_to_component_map.get(flow) or self.flow_to_component_map.get(
            "default", {}
        )
        return str(component_map.get(role, default_key) or default_key)

    def _component_for_role(self, *, agent_name: str, role: str, default_key: str) -> Any:
        return self._component_registry[
            self._component_key_for_role(agent_name=agent_name, role=role, default_key=default_key)
        ]

    def _components_for_role(self, *, role: str, default_key: str) -> list[Any]:
        keys: list[str] = []
        for component_map in self.flow_to_component_map.values():
            key = str(component_map.get(role, "") or "").strip()
            if key:
                keys.append(key)
        if not keys:
            keys.append(default_key)
        seen: set[str] = set()
        components: list[Any] = []
        for key in keys:
            if key in seen:
                continue
            if key not in self._component_registry:
                raise KeyError(f"Unknown {role} component key {key!r}.")
            seen.add(key)
            components.append(self._component_registry[key])
        return components

    def _flow_for_candidates(self, candidate_agents: Sequence[Any]) -> str:
        flows = {
            self._flow_for_agent(str(getattr(agent, "name", "") or ""))
            for agent in candidate_agents
            if str(getattr(agent, "name", "") or "").strip()
        }
        if len(flows) == 1:
            return next(iter(flows))
        return "default"

    def action_prompt(self, agent_name: str) -> ActionSpec:
        """Return the direct action prompt for one agent."""
        component = self._component_for_role(
            agent_name=agent_name,
            role="action_prompt",
            default_key="action_prompt",
        )
        direct = getattr(component, "action_prompt", None)
        if not callable(direct):
            raise TypeError(
                "Action-prompt component must expose action_prompt(agent_name) in the native runtime."
            )
        return cast(ActionSpec, direct(agent_name))

    def acting_agents(self, candidate_agents: Sequence[Any]) -> list[str]:
        """Return selected agents for this turn."""
        candidates = {agent.name: agent for agent in candidate_agents}
        flow = self._flow_for_candidates(candidate_agents)
        component_key = str(
            (
                self.flow_to_component_map.get(flow)
                or self.flow_to_component_map.get("default", {})
            ).get("next_acting", "next_acting")
            or "next_acting"
        )
        component = self._component_registry[component_key]
        direct = getattr(component, "acting_agent_names", None)
        if not callable(direct):
            raise TypeError(
                "Next-acting component must expose acting_agent_names() in the native runtime."
            )
        names: list[str] = []
        for agent_name in [str(name).strip() for name in direct() if str(name).strip()]:
            if agent_name not in candidates:
                _LOGGER.warning(
                    "Ignoring unknown next_acting agent '%s' from game master '%s'.",
                    agent_name,
                    self.name,
                )
                continue
            names.append(agent_name)
        return names

    def make_observation(self, agent_name: str) -> str:
        """Build an observation for one agent."""
        component = self._component_for_role(
            agent_name=agent_name,
            role="observe",
            default_key="observe",
        )
        direct = getattr(component, "make_observation", None)
        if not callable(direct):
            raise TypeError(
                "Observation component must expose make_observation(agent_name) in the native runtime."
            )
        result = str(direct(agent_name))
        return result

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        """Resolve one agent's raw action text against the backend."""
        component = self._component_for_role(
            agent_name=agent_name,
            role="resolve",
            default_key="resolve",
        )
        direct = getattr(component, "resolve_action", None)
        if not callable(direct):
            raise TypeError(
                "Resolve component must expose resolve_action(agent_name, action) in the native runtime."
            )
        result = str(direct(agent_name, action))
        return result

    def get_state(self) -> dict[str, Any]:
        """Return serializable component state for checkpoints."""
        state: dict[str, Any] = {"initialized": self._initialized, "components": {}}
        component_state = state["components"]
        for key, component in self._component_registry.items():
            getter = getattr(component, "get_state", None)
            if callable(getter):
                component_state[key] = getter()
        return state

    def set_state(self, state: Mapping[str, Any]) -> None:
        """Restore component state from checkpoints."""
        state = dict(state or {})
        component_states = state.get("components", {})
        if not isinstance(component_states, Mapping):
            return
        for key, value in component_states.items():
            component = self._component_registry.get(str(key))
            setter = getattr(component, "set_state", None)
            if callable(setter):
                setter(value)


class _GameMasterWiring:
    """Internal single-flow GM wiring helper."""

    def __init__(
        self,
        *,
        model: Any | None = None,
        agents: Sequence[Any] = (),
        **params: Any,
    ) -> None:
        if "entities" in params:
            raise ValueError("GameMaster params use removed `entities`; pass `agents` instead.")
        for removed in ("runtime" + "_config", "sm" + "_user_data", "social" + "_media_action"):
            if removed in params:
                raise ValueError("GameMaster params include a removed legacy backend key.")
        nested_params = params.pop("params", None)
        if isinstance(nested_params, Mapping):
            params = {**dict(nested_params), **params}
        self.model = model
        self.agents = tuple(agents)
        self.params = {
            "name": "environment_game-master",
            "sim_role": {},
            "environment_data": {},
            "backend_config": {},
            "components": {},
            "action_prompt_template": "",
            "action_mode": "custom",
            "tool_calling_mode": "none",
            "timeline_mode": "follower_chronological",
            "timeline_posts": 10,
            "timeline_config": {},
            **dict(params),
        }

    def _is_shared_flow_mode(self) -> bool:
        return False

    def build_generic_prompt(
        self,
        *,
        backend: Any,
        tool_calling_mode: str,
        gm_prompt_cfg: Mapping[str, Any] | None = None,
    ) -> str:
        """Build generic action prompt from backend action catalog for this GM.

        Generic prompts are generated at GM runtime so they reflect the current backend
        instance and enabled action set.
        """
        return build_generic_action_prompt(
            backend=backend,
            tool_calling_mode=tool_calling_mode,
            gm_prompt_cfg=gm_prompt_cfg,
            output_style=str(self.params.get("output_style", "") or ""),
            add_action_count_guidance=bool(self.params.get("add_action_count_guidance", True)),
        )

    # ------------------------------------------------------------------
    # build_runtime_kwargs helpers
    # ------------------------------------------------------------------

    def _create_backend_app(self) -> Any:
        """Instantiate the backend app and configure enabled actions."""
        backend_cfg = dict(self.params.get("backend_config") or {})
        backend_type = str(backend_cfg.get("backend_type") or "").strip()
        if not backend_type:
            raise ValueError("GameMaster backend_config.backend_type is required.")
        output_rootname = str(backend_cfg.get("output_rootname") or "")
        action_logger = EventLogger(
            "action",
            os.path.join(output_rootname, "action_events.jsonl"),
        )
        action_logger.episode_idx = 0

        db_path = os.path.join(output_rootname, f"{backend_type}.db")
        backend = create_backend_app(
            backend_type=backend_type,
            action_logger=action_logger,
            perform_operations=bool(backend_cfg.get("perform_operations", False)),
            app_description=str(backend_cfg.get("app_description") or ""),
            db_path=db_path,
            class_path=backend_cfg.get("class_path"),
            params=dict(backend_cfg.get("params") or {}),
        )

        enabled_actions_cfg = backend_cfg.get("enabled_actions")
        if enabled_actions_cfg is not None:
            if isinstance(enabled_actions_cfg, Sequence) and not isinstance(
                enabled_actions_cfg, (str, bytes)
            ):
                enabled_actions = [str(action).strip() for action in enabled_actions_cfg]
            else:
                enabled_actions = [str(enabled_actions_cfg).strip()]

            turn_policy_built_in = str(backend_cfg.get("turn_policy_built_in") or "").strip()
            enabled_actions_upper = {name.upper() for name in enabled_actions if name}
            if turn_policy_built_in == "open_ended" and "FINISHED" not in enabled_actions_upper:
                enabled_actions.append("FINISHED")

            backend.set_enabled_actions(enabled_actions)

        return backend

    @staticmethod
    def _resolve_gm_components_cfg(params: Mapping[str, Any]) -> dict[str, Any]:
        """Extract and validate the gm.components config dict."""
        gm_components_cfg = dict(params.get("components") or {})
        if "recommend" in gm_components_cfg:
            raise ValueError(
                "`env.gm.components.recommend` has been removed. "
                "Use `env.gm.components.update` with built_in='social_recommendation'."
            )
        return gm_components_cfg

    def _validate_observe_slot(self, backend_type: str, observe_slot: Mapping[str, Any]) -> None:
        """Validate timeline config when the observe slot declares one."""
        params = dict(observe_slot.get("params") or {}) if isinstance(observe_slot, Mapping) else {}
        timeline_mode = params.get("timeline_mode")
        if not timeline_mode:
            return
        timeline_mode = str(timeline_mode)
        supported_timeline_modes = {
            "twitter_like": {
                "follower_chronological",
                "pure_recsys",
                "hybrid_recsys_follower",
                "curated_global",
            },
            "reddit_like": {
                "follower_chronological",
                "pure_recsys",
                "hybrid_recsys_follower",
            },
            "mastodon": {"follower_chronological"},
        }
        allowed_modes = supported_timeline_modes.get(backend_type, {"follower_chronological"})
        if timeline_mode not in allowed_modes:
            raise ValueError(
                f"Unsupported timeline_mode='{timeline_mode}' for backend '{backend_type}'. "
                f"Supported: {sorted(allowed_modes)}"
            )

    # ------------------------------------------------------------------

    def build_runtime_kwargs(
        self,
        model: Any | None = None,
    ) -> dict[str, Any]:
        """Build keyword arguments for the native game-master runtime."""
        model = model or self.model
        name = str(self.params.get("name"))
        environment_data = dict(self.params["environment_data"])
        action_prompt_template = str(self.params.get("action_prompt_template") or "")
        backend_cfg = dict(self.params.get("backend_config") or {})
        backend_type = str(backend_cfg.get("backend_type") or "")
        backend = self._create_backend_app()

        agent_names = [agent.name for agent in self.agents]
        sim_roles = dict(environment_data.get("sim_roles", {}) or {})
        agent_flow_tags = dict(environment_data.get("agent_flow_tags", {}) or {})
        gm_orchestration = dict(environment_data.get("gm_orchestration", {}) or {})
        gm_prompt_cfg = dict(gm_orchestration.get("prompt", {}) or {})

        gm_components_cfg = self._resolve_gm_components_cfg(self.params)

        initializer_cfg = dict(gm_components_cfg.get("initialize") or {})
        if not initializer_cfg:
            raise ValueError("Native GameMaster requires `env.gm.components.initialize`.")

        _ACTION_MODE_TO_RESOLVE = {"custom": "parsed_action", "generic": "generic_action"}
        resolve_slot = dict(gm_components_cfg.get("resolve", {}))
        if not resolve_slot:
            resolve_slot = {
                "built_in": _ACTION_MODE_TO_RESOLVE.get(
                    str(self.params.get("action_mode") or "custom"), "parsed_action"
                ),
            }

        tool_calling_mode = str(self.params.get("tool_calling_mode") or "none").strip().lower()
        action_mode = str(self.params.get("action_mode") or "custom").strip().lower()
        if action_mode == "generic":
            action_prompt_template = self.build_generic_prompt(
                backend=backend,
                tool_calling_mode=tool_calling_mode,
                gm_prompt_cfg=gm_prompt_cfg,
            )

        enable_tool_calling = tool_calling_mode in {"single", "multi"}
        action_output_mode = str(resolve_slot.get("built_in", "parsed_action") or "parsed_action")

        # Build all component slots
        initialize_component = build_initialize_component(initializer_cfg)
        next_actor = build_next_acting_component(
            gm_components_cfg.get("next_acting"),
            agent_names=agent_names,
            sim_roles=sim_roles,
        )
        action_prompt_component = build_action_prompt_component(
            gm_components_cfg.get("action_prompt"),
            backend=backend,
            action_prompt_template=action_prompt_template,
            enable_tool_calling=enable_tool_calling,
            tool_calling_mode=tool_calling_mode,
        )

        observe_slot = dict(gm_components_cfg.get("observe", {}))
        observe_params = dict(observe_slot.get("params") or {})
        episode_observation_flow = observe_params.get("episode_observation_flow", "fixed_pre")
        if isinstance(episode_observation_flow, list):
            episode_observation_flow = (
                episode_observation_flow[0] if episode_observation_flow else "fixed_pre"
            )

        self._validate_observe_slot(backend_type, observe_slot)

        make_observation = build_observe_component(
            observe_slot,
            model=model,
            agent_names=agent_names,
            backend=backend,
            agent_flow_tags=agent_flow_tags,
            episode_observation_flow=str(episode_observation_flow),
        )
        resolve_component = build_resolve_component(
            resolve_slot,
            backend=backend,
            model=model,
            action_prompt_template=action_prompt_template,
            agents_by_name={agent.name: agent for agent in self.agents},
        )
        update_slot = dict(gm_components_cfg.get("update", {}))
        update_component = build_update_component(
            update_slot,
            backend=backend,
            backend_type=backend_type,
        )

        if hasattr(update_component, "validate_recsys_types") and callable(
            update_component.validate_recsys_types
        ):
            update_component.validate_recsys_types()

        component_slots = GameMasterComponentSlots(
            initialize=initialize_component,
            next_acting=next_actor,
            action_prompt=action_prompt_component,
            observe=make_observation,
            resolve=resolve_component,
            update=update_component,
        )

        return {
            "name": name,
            "model": model,
            "backend": backend,
            "backend_type": backend_type,
            "component_slots": component_slots,
            "environment_data": environment_data,
            "action_prompt_template": action_prompt_template,
            "action_output_mode": action_output_mode,
            "activity_transition_rates": dict(
                dict(gm_components_cfg.get("next_acting", {}) or {})
                .get("params", {})
                .get("activity_transition_rates", {})
                or {}
            ),
            "agent_flow_tags": agent_flow_tags,
            "gm_orchestration": gm_orchestration,
            "shared_flow_mode": self._is_shared_flow_mode(),
            "enable_tool_calling": enable_tool_calling,
        }

    def build(
        self,
        model: Any | None = None,
    ) -> EnvironmentGameMaster:
        """Build and return the configured environment game master."""
        return EnvironmentGameMaster(**self.build_runtime_kwargs(model=model))
