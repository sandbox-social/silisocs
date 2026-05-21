"""Direct runtime object construction helpers."""

from __future__ import annotations

import importlib
import inspect
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.runtime.construction.specs import RuntimeRole, RuntimeSpec
from silisocs.runtime.language_models import LanguageModel
from silisocs.runtime.telemetry import SimMetricsCollector

_GM_METHODS = (
    "initialize",
    "update",
    "acting_agents",
    "action_prompt",
    "make_observation",
    "resolve_action",
)


@dataclass
class RuntimeObjects:
    """Objects assembled by the runner and consumed by the engine."""

    agents: list[Agent] = field(default_factory=list)
    game_masters: list[Any] = field(default_factory=list)
    object_specs: dict[str, RuntimeSpec] = field(default_factory=dict)
    checkpoint_counter: int = 0

    def default_model(self, models: dict[str, LanguageModel]) -> LanguageModel:
        """Return the default model for runtime initialization."""
        if not models:
            raise ValueError("No language models available for runtime initialization.")
        return next(iter(models.values()))

    def game_masters_by_sequence(self) -> list[Any]:
        """Return game masters in configured orchestration order."""

        def _gm_sequence(game_master: Any) -> int:
            spec = self.object_specs.get(game_master.name)
            if not spec:
                return 0
            user_data = spec.params.get("environment_data", spec.params.get("sm_user_data", {}))
            if not isinstance(user_data, dict):
                return 0
            orchestration = user_data.get("gm_orchestration", {})
            if not isinstance(orchestration, dict):
                return 0
            try:
                return int(orchestration.get("sequence", 0))
            except (TypeError, ValueError):
                return 0

        return sorted(self.game_masters, key=lambda gm: (_gm_sequence(gm), gm.name))


def build_runtime_objects(
    *,
    specs: Sequence[RuntimeSpec],
    models: dict[str, LanguageModel],
    object_to_model: dict[str, str],
) -> RuntimeObjects:
    """Build agents and game masters from direct runtime specs."""
    runtime = RuntimeObjects()
    agent_specs = [spec for spec in specs if spec.role == RuntimeRole.AGENT]
    gm_specs = [spec for spec in specs if spec.role == RuntimeRole.GAME_MASTER]

    for spec in agent_specs:
        t0 = time.time()
        agent = add_agent(
            runtime=runtime, spec=spec, models=models, object_to_model=object_to_model
        )
        print(f"Built agent '{agent.name}' in {time.time() - t0:.2f}s")

    for spec in gm_specs:
        t0 = time.time()
        gm = add_game_master(
            runtime=runtime, spec=spec, models=models, object_to_model=object_to_model
        )
        print(f"Built game master '{gm.name}' in {time.time() - t0:.2f}s")

    return runtime


def add_agent(
    *,
    runtime: RuntimeObjects,
    spec: RuntimeSpec,
    models: dict[str, LanguageModel],
    object_to_model: dict[str, str],
    state: dict[str, Any] | None = None,
) -> Agent:
    """Build and add one agent."""
    if spec.role != RuntimeRole.AGENT:
        raise ValueError("Runtime spec role must be agent.")
    name = str(spec.params["name"])
    if any(agent.name == name for agent in runtime.agents):
        raise ValueError(f"Duplicate agent name: {name}")
    model = models[object_to_model[name]]
    if spec.compat == "concordia":
        agent = _build_concordia_agent(spec=spec, model=model)
    elif spec.compat:
        raise ValueError(f"Unsupported agent compat value: {spec.compat}")
    else:
        if spec.class_path.startswith("silisocs.agents.concordia."):
            raise TypeError(
                f"Agent class '{spec.class_path}' is Concordia compatibility code. "
                "Set `compat: concordia` so it is wrapped by ConcordiaAgentAdapter."
            )
        cls = _load_object(spec.class_path)
        built = _instantiate_with_supported_kwargs(cls, {"model": model, **spec.params})
        if not isinstance(built, Agent):
            raise TypeError(
                f"Agent class '{spec.class_path}' returned {type(built).__name__}, "
                "not a native silisocs Agent. Use `compat: concordia` only for legacy agents."
            )
        agent = built
    if state:
        setter = getattr(agent, "set_state", None)
        if not callable(setter):
            raise TypeError(f"Agent '{name}' does not support checkpoint state restore.")
        setter(state)
    runtime.agents.append(agent)
    runtime.object_specs[agent.name] = spec
    return agent


def add_game_master(
    *,
    runtime: RuntimeObjects,
    spec: RuntimeSpec,
    models: dict[str, LanguageModel],
    object_to_model: dict[str, str],
    state: dict[str, Any] | None = None,
) -> Any:
    """Build and add one game master."""
    if spec.role != RuntimeRole.GAME_MASTER:
        raise ValueError("Runtime spec role must be game_master.")
    name = str(spec.params["name"])
    if any(gm.name == name for gm in runtime.game_masters):
        raise ValueError(f"Duplicate game master name: {name}")
    model = models[object_to_model[name]]
    if spec.compat == "concordia":
        gm = _build_concordia_game_master(spec=spec, model=model, agents=runtime.agents)
    elif spec.compat:
        raise ValueError(f"Unsupported game-master compat value: {spec.compat}")
    else:
        cls = _load_object(spec.class_path)
        built = _instantiate_with_supported_kwargs(
            cls,
            {"model": model, "agents": runtime.agents, **spec.params},
        )
        missing = [method for method in _GM_METHODS if not callable(getattr(built, method, None))]
        if missing:
            raise TypeError(
                f"Game master class '{spec.class_path}' is missing native methods: "
                f"{', '.join(missing)}."
            )
        gm = built
    if state:
        setter = getattr(gm, "set_state", None)
        if not callable(setter):
            raise TypeError(f"Game master '{name}' does not support checkpoint state restore.")
        setter(state)
    runtime.object_specs[gm.name] = spec
    runtime.game_masters.append(gm)
    return gm


def construct_runtime_with_metrics(
    *,
    specs: Sequence[RuntimeSpec],
    models: dict[str, LanguageModel],
    object_to_model: dict[str, str],
) -> RuntimeObjects:
    """Build runtime objects under the shared metrics phase."""
    with SimMetricsCollector.get().phase("runtime_construction"):
        return build_runtime_objects(
            specs=specs,
            models=models,
            object_to_model=object_to_model,
        )


def _load_object(class_path: str) -> Any:
    module_path, name = str(class_path).rsplit(".", 1)
    return getattr(importlib.import_module(module_path), name)


def _instantiate_with_supported_kwargs(cls: Any, kwargs: dict[str, Any]) -> Any:
    try:
        params = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        return cls(**kwargs)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return cls(**kwargs)
    supported = {
        name
        for name, param in params.items()
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    unsupported = sorted(set(kwargs) - supported)
    if unsupported:
        raise ValueError(
            f"Unsupported config param(s) for {cls.__module__}.{cls.__name__}: "
            f"{unsupported}. Supported params: {sorted(supported)}"
        )
    return cls(**{key: value for key, value in kwargs.items() if key in supported})


def _build_concordia_agent(*, spec: RuntimeSpec, model: LanguageModel) -> Agent:
    adapter = importlib.import_module("silisocs.adapters.concordia")
    prefab_cls = _load_object(spec.class_path)
    prefab = _instantiate_with_supported_kwargs(prefab_cls, {"params": spec.params})
    memory_bank = adapter.make_concordia_memory_bank(
        str(spec.params.get("memory_backend", "list") or "list")
    )
    built = prefab.build(model=model, memory_bank=memory_bank)
    return adapter.ConcordiaAgentAdapter(built, model)


def _build_concordia_game_master(
    *,
    spec: RuntimeSpec,
    model: LanguageModel,
    agents: list[Agent],
) -> Any:
    adapter = importlib.import_module("silisocs.adapters.concordia")
    prefab_cls = _load_object(spec.class_path)
    prefab = _instantiate_with_supported_kwargs(prefab_cls, {"params": spec.params})
    if hasattr(prefab, "entities"):
        prefab.entities = agents
    memory_bank = adapter.make_concordia_memory_bank(
        str(spec.params.get("memory_backend", "list") or "list")
    )
    built = prefab.build(model=model, memory_bank=memory_bank)
    return adapter.ConcordiaGameMasterAdapter(built)


def _has_native_gm_methods(value: Any) -> bool:
    return all(callable(getattr(value, method, None)) for method in _GM_METHODS)
