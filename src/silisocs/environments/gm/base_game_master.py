"""Shared native game-master construction helpers."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from omegaconf import OmegaConf

from silisocs.environments.backends.factory import create_environment_app
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
    prompt_additions_from_cfg,
)
from silisocs.runtime.types import ActionOutput, ActionSpec

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameMasterComponentSlots:
    """Typed native component slots owned by a game master."""

    initialize: Any
    next_acting: Any
    action_prompt: Any
    observe: Any
    resolve: Any
    update: Any


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


def _env_cfg(cfg: Any) -> Any:
    """_env_cfg.

    :param Any cfg:
    :type cfg: Any

    :returns: Any
    :rtype: Any
    """
    return getattr(cfg, "env", getattr(cfg, "environment", object()))


def _runtime_cfg(params: Mapping[str, Any]) -> Any:
    """Return the explicit runner-provided config view for GM construction."""
    raw = params.get("runtime_config")
    if raw is None:
        raise ValueError(
            "GameMaster requires `runtime_config` in params. Runtime construction must pass "
            "the runner-built config view explicitly."
        )
    if isinstance(raw, Mapping):
        return OmegaConf.create(dict(raw))
    return raw


def _compute_activity_rates(
    user_data: dict[str, Any],
) -> dict[str, dict[str, float]]:
    """Map each agent to its role's activity transition rates."""
    rates: dict[str, dict[str, float]] = {}
    sim_role_parameters = user_data.get("sim_role_parameters", {}) or {}
    transition_rates = dict(sim_role_parameters.get("activity_transition_rates", {}) or {})
    for agent, role in user_data["sim_roles"].items():
        rates[agent] = dict(
            transition_rates.get(
                role,
                {
                    "inactive_to_active": 1.0,
                    "active_to_inactive": 0.0,
                },
            )
        )
    return rates


def build_generic_action_prompt(
    *,
    cfg: Any,
    sm_app: Any,
    tool_calling_mode: str,
    gm_prompt_cfg: Mapping[str, Any] | None = None,
) -> str:
    """Build a generic action prompt from a backend action catalog."""
    gm_prompt_cfg = dict(gm_prompt_cfg or {})
    output_style = str(gm_prompt_cfg.get("output_style", "") or "").strip()
    if not output_style:
        output_style = str(getattr(_env_cfg(cfg), "output_style", "") or "")

    additions = prompt_additions_from_cfg(cfg)
    base_prompt = str(sm_app.generate_generic_action_prompt() or "").strip()
    return compile_action_prompt(
        base_prompt=base_prompt,
        output_style=output_style,
        tool_calling_mode=tool_calling_mode,
        additions=PromptAdditions(
            add_action_count_guidance=additions.add_action_count_guidance,
        ),
    )


class EnvironmentGameMaster(BaseGameMaster):
    """Direct native game master coordinating one backend and component set."""

    def __init__(
        self,
        *,
        name: str,
        model: Any,
        app: Any,
        component_slots: GameMasterComponentSlots,
        component_registry: Mapping[str, Any] | None = None,
        user_data: Mapping[str, Any],
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
        self.app = app
        self.sm_app = app
        self.env_app = app
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
        self.user_data = dict(user_data or {})
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
        nested_params = params.pop("params", None)
        if isinstance(nested_params, Mapping):
            params = {**dict(nested_params), **params}
        self.model = model
        self.agents = tuple(agents)
        self.params = {
            "name": "environment_game-master",
            "calls_to_action": {},
            "app_module_path": "",
            "sim_role": {},
            "environment_data": {},
            "sm_user_data": {},
            "app_description": "",
            **dict(params),
        }

    def _is_shared_flow_mode(self) -> bool:
        """_is_shared_flow_mode.

        :returns: bool
        :rtype: bool
        """
        return False

    def build_generic_prompt(
        self,
        *,
        cfg: Any,
        sm_app: Any,
        tool_calling_mode: str,
        gm_prompt_cfg: Mapping[str, Any] | None = None,
    ) -> str:
        """Build generic action prompt from backend action catalog for this GM.

        Generic prompts are generated at GM runtime so they reflect the current backend
        instance and enabled action set.
        """
        return build_generic_action_prompt(
            cfg=cfg,
            sm_app=sm_app,
            tool_calling_mode=tool_calling_mode,
            gm_prompt_cfg=gm_prompt_cfg,
        )

    def build_runtime_kwargs(
        self,
        model: Any | None = None,
    ) -> dict[str, Any]:
        """Build keyword arguments for the native game-master runtime."""
        model = model or self.model
        name = str(self.params.get("name"))
        calls_to_action = self.params.get("calls_to_action", {})
        user_data = self.params.get("environment_data") or self.params["sm_user_data"]
        call_to_sm_action = calls_to_action.get(
            "environment_action",
            calls_to_action.get("social_media_action", ""),
        )

        cfg = _runtime_cfg(self.params)
        action_logger = EventLogger(
            "action",
            os.path.join(cfg.output_rootname, "action_events.jsonl"),
        )
        action_logger.episode_idx = 0

        platform_type = getattr(_env_cfg(cfg), "platform_type", "twitter_like")
        db_path = os.path.join(cfg.output_rootname, f"{platform_type}.db")
        sm_app = create_environment_app(
            platform_type=platform_type,
            action_logger=action_logger,
            perform_operations=getattr(_env_cfg(cfg), "use_server", False),
            app_description=self.params.get("app_description", ""),
            db_path=db_path,
            app_class_path=OmegaConf.select(cfg, "env.app.class_path", default=None),
            app_params=OmegaConf.select(cfg, "env.app.params", default={}) or {},
        )
        sm_app.platform_type = platform_type  # type: ignore[attr-defined]

        enabled_actions_cfg = getattr(_env_cfg(cfg), "enabled_actions", None)
        if enabled_actions_cfg is not None:
            if isinstance(enabled_actions_cfg, Sequence) and not isinstance(
                enabled_actions_cfg, (str, bytes)
            ):
                enabled_actions = [str(action).strip() for action in enabled_actions_cfg]
            else:
                enabled_actions = [str(enabled_actions_cfg).strip()]

            turn_policy_built_in = str(
                OmegaConf.select(cfg, "sim.engine.turn_policy.built_in", default="") or ""
            ).strip()
            enabled_actions_upper = {name.upper() for name in enabled_actions if name}
            if turn_policy_built_in == "open_ended" and "FINISHED" not in enabled_actions_upper:
                enabled_actions.append("FINISHED")

            sm_app.set_enabled_actions(enabled_actions)

        agent_names = [agent.name for agent in self.agents]

        env_cfg = _env_cfg(cfg)
        activity_rates = _compute_activity_rates(user_data)
        agent_flow_tags = dict(user_data.get("agent_flow_tags", {}) or {})
        gm_orchestration = dict(user_data.get("gm_orchestration", {}) or {})
        gm_prompt_cfg = dict(gm_orchestration.get("prompt", {}) or {})
        gm_components_cfg: dict[str, Any] = {}
        env_gm_cfg = getattr(_env_cfg(cfg), "gm", None)
        if env_gm_cfg is not None and getattr(env_gm_cfg, "components", None) is not None:
            gm_components_cfg = cast(
                dict[str, Any],
                OmegaConf.to_container(
                    env_gm_cfg.components,
                    resolve=True,
                ),
            )
        elif hasattr(cfg.env, "gm") and getattr(cfg.env.gm, "components", None) is not None:
            gm_components_cfg = cast(
                dict[str, Any],
                OmegaConf.to_container(
                    cfg.env.gm.components,
                    resolve=True,
                ),
            )
        if "recommend" in gm_components_cfg:
            raise ValueError(
                "`env.gm.components.recommend` has been removed. "
                "Use `env.gm.components.update` with built_in='social_recommendation'."
            )
        initializer_cfg = dict(
            gm_components_cfg.get("initialize") or self.params.get("initializer") or {}
        )
        if not initializer_cfg:
            raise ValueError(
                "Native GameMaster requires `env.gm.components.initialize` "
                "(or a runner-provided initializer during migration)."
            )

        # Map action_mode to default resolve component
        # Note: tool_calling is NOT an action_mode, only a resolve component option
        action_mode_to_resolve_map = {
            "custom": "parsed_action",
            "generic": "generic_action",
        }
        resolve_slot = dict(gm_components_cfg.get("resolve", {}))
        if not resolve_slot:
            # Use default resolver based on action_mode
            resolve_slot = {
                "built_in": action_mode_to_resolve_map.get(
                    getattr(cfg.sim, "action_mode", "custom"), "parsed_action"
                ),
            }

        # Determine if tool-calling is enabled from explicit mode config.
        tool_calling_mode = (
            str(OmegaConf.select(cfg, "sim.tool_calling.mode", default="none") or "none")
            .strip()
            .lower()
        )
        action_mode = str(getattr(cfg.sim, "action_mode", "custom") or "custom").strip().lower()
        if action_mode == "generic":
            call_to_sm_action = self.build_generic_prompt(
                cfg=cfg,
                sm_app=sm_app,
                tool_calling_mode=tool_calling_mode,
                gm_prompt_cfg=gm_prompt_cfg,
            )

        enable_tool_calling = tool_calling_mode in {"single", "multi"}
        action_output_mode = str(resolve_slot.get("built_in", "parsed_action") or "parsed_action")

        initialize_component = build_initialize_component(initializer_cfg)
        next_actor = build_next_acting_component(
            gm_components_cfg.get("next_acting"),
            agent_names=agent_names,
            activity_transition_rates=activity_rates,
        )
        action_prompt_component = build_action_prompt_component(
            gm_components_cfg.get("action_prompt"),
            app=sm_app,
            action_prompt_template=call_to_sm_action,
            enable_tool_calling=enable_tool_calling,
        )
        observe_slot = dict(gm_components_cfg.get("observe", {}))
        observe_params = dict(observe_slot.get("params") or {})
        episode_observation_flow = observe_params.get("episode_observation_flow", "fixed_pre")
        if isinstance(episode_observation_flow, list):
            episode_observation_flow = (
                episode_observation_flow[0] if episode_observation_flow else "fixed_pre"
            )

        timeline_mode = str(
            getattr(_env_cfg(cfg), "timeline_mode", None) or "follower_chronological"
        )
        timeline_posts = int(getattr(_env_cfg(cfg), "timeline_posts", 10) or 10)
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
        allowed_modes = supported_timeline_modes.get(platform_type, {"follower_chronological"})
        if timeline_mode not in allowed_modes:
            raise ValueError(
                f"Unsupported timeline_mode='{timeline_mode}' for platform '{platform_type}'. "
                f"Supported: {sorted(allowed_modes)}"
            )
        timeline_config = {}
        if hasattr(_env_cfg(cfg), "timeline_config"):
            timeline_config = (
                cast(
                    dict[str, Any],
                    OmegaConf.to_container(_env_cfg(cfg).timeline_config, resolve=True),
                )
                if isinstance(_env_cfg(cfg).timeline_config, dict)
                else {}
            )
        elif hasattr(cfg.env, "timeline_config"):
            timeline_config = (
                cast(
                    dict[str, Any],
                    OmegaConf.to_container(cfg.env.timeline_config, resolve=True),
                )
                if isinstance(cfg.env.timeline_config, dict)
                else {}
            )

        make_observation = build_observe_component(
            observe_slot,
            model=model,
            agent_names=agent_names,
            sm_app=sm_app,
            agent_flow_tags=agent_flow_tags,
            episode_observation_flow=str(episode_observation_flow),
            timeline_mode=timeline_mode,
            timeline_posts=timeline_posts,
            timeline_config=timeline_config,
        )
        resolve_component = build_resolve_component(
            resolve_slot,
            sm_app=sm_app,
            model=model,
            action_prompt_template=call_to_sm_action,
            agents_by_name={agent.name: agent for agent in self.agents},
        )
        update_slot = dict(gm_components_cfg.get("update", {}))
        update_component = build_update_component(
            update_slot,
            sm_app=sm_app,
            platform_type=platform_type,
            timeline_mode=timeline_mode,
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
            "app": sm_app,
            "component_slots": component_slots,
            "user_data": user_data,
            "action_prompt_template": call_to_sm_action,
            "action_output_mode": action_output_mode,
            "activity_transition_rates": activity_rates,
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
