"""Direct runtime object construction helpers."""

from __future__ import annotations

import importlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.runtime.class_loading import (
    instantiate_with_supported_kwargs,
    load_attr,
    supported_kwargs,
)
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

    def game_masters_by_sequence(self) -> list[Any]:
        """Return game masters in configured orchestration order."""

        def _gm_sequence(game_master: Any) -> int:
            spec = self.object_specs.get(game_master.name)
            if not spec:
                return 0
            # Already coerced to int when the GM specs were built
            # (construction/game_masters.py), so there is nothing to guard against.
            return int(spec.params.get("sequence", 0))

        return sorted(self.game_masters, key=lambda gm: (_gm_sequence(gm), gm.name))


def _constructor_params(spec: RuntimeSpec) -> dict[str, Any]:
    """Spec params minus the per-class ``model`` block.

    The ``model`` entry in spec params is a framework directive — consumed by
    ``build_deduped_models`` to select the built ``LanguageModel`` — never a
    constructor kwarg. Splatting it through would clobber the built model with
    the raw config mapping. It stays on the spec itself (checkpoint restore
    re-reads it); it just never reaches a constructor.
    """
    return {key: value for key, value in spec.params.items() if key != "model"}


def build_runtime_objects(
    *,
    specs: Sequence[RuntimeSpec],
    models: dict[str, LanguageModel],
    object_to_model: dict[str, str],
    memory_factory: Any | None = None,
) -> RuntimeObjects:
    """Build agents and game masters from direct runtime specs."""
    runtime = RuntimeObjects()
    agent_specs = [spec for spec in specs if spec.role == RuntimeRole.AGENT]
    gm_specs = [spec for spec in specs if spec.role == RuntimeRole.GAME_MASTER]

    t0 = time.time()
    for spec in agent_specs:
        add_agent(
            runtime=runtime,
            spec=spec,
            models=models,
            object_to_model=object_to_model,
            memory_factory=memory_factory,
        )
    if agent_specs:
        print(f"Built {len(agent_specs)} agents in {time.time() - t0:.2f}s")

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
    memory_factory: Any | None = None,
) -> Agent:
    """Build and add one agent."""
    if spec.role != RuntimeRole.AGENT:
        raise ValueError("Runtime spec role must be agent.")
    name = str((spec.params or {}).get("name", "")).strip()
    if not name:
        raise ValueError("Agent runtime specs must include a non-empty `name` param.")
    # O(1) via the spec index (every added object registers there); scanning
    # runtime.agents per insertion is O(N^2) across construction.
    existing = runtime.object_specs.get(name)
    if existing is not None and existing.role == RuntimeRole.AGENT:
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
        cls = load_attr(spec.class_path)
        agent_kwargs: dict[str, Any] = {"model": model, **_constructor_params(spec)}
        # Inject the memory-policy factory only into agents that accept it (a
        # framework kwarg, so it is filtered rather than raising for agents —
        # e.g. FixedAgent — that don't take it; user params still raise on typo).
        accepted = supported_kwargs(cls)
        if memory_factory is not None and (accepted is None or "memory_policy" in accepted):
            agent_kwargs["memory_policy"] = memory_factory
        built = instantiate_with_supported_kwargs(
            cls,
            agent_kwargs,
            config_path=f"agents.persona_pipeline params for agent '{name}'",
        )
        if not isinstance(built, Agent):
            raise TypeError(
                f"Agent class '{spec.class_path}' returned {type(built).__name__}, "
                "not a native silisocs Agent. Use `compat: concordia` only for "
                "Concordia-shaped agents."
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
    existing = runtime.object_specs.get(name)
    if existing is not None and existing.role == RuntimeRole.GAME_MASTER:
        raise ValueError(f"Duplicate game master name: {name}")
    model = models[object_to_model[name]]
    if spec.compat == "concordia":
        gm = _build_concordia_game_master(spec=spec, model=model, agents=runtime.agents)
    elif spec.compat:
        raise ValueError(f"Unsupported game-master compat value: {spec.compat}")
    else:
        cls = load_attr(spec.class_path)
        built = instantiate_with_supported_kwargs(
            cls,
            {"model": model, "agents": runtime.agents, **_constructor_params(spec)},
            config_path=f"env.gm params for game master '{name}'",
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
    memory_factory: Any | None = None,
) -> RuntimeObjects:
    """Build runtime objects under the shared metrics phase."""
    with SimMetricsCollector.get().phase("runtime_construction"):
        return build_runtime_objects(
            specs=specs,
            models=models,
            object_to_model=object_to_model,
            memory_factory=memory_factory,
        )


def _build_concordia_agent(*, spec: RuntimeSpec, model: LanguageModel) -> Agent:
    adapter = importlib.import_module("silisocs.adapters.concordia")
    builder_cls = load_attr(spec.class_path)
    builder = instantiate_with_supported_kwargs(builder_cls, {"params": _constructor_params(spec)})
    memory_bank = adapter.make_concordia_memory_bank(
        str(spec.params.get("memory_backend", "list") or "list")
    )
    built = builder.build(model=model, memory_bank=memory_bank)
    return adapter.ConcordiaAgentAdapter(built, model)


def _build_concordia_game_master(
    *,
    spec: RuntimeSpec,
    model: LanguageModel,
    agents: list[Agent],
) -> Any:
    adapter = importlib.import_module("silisocs.adapters.concordia")
    builder_cls = load_attr(spec.class_path)
    builder = instantiate_with_supported_kwargs(builder_cls, {"params": _constructor_params(spec)})
    if hasattr(builder, "agents"):
        builder.agents = agents
    memory_bank = adapter.make_concordia_memory_bank(
        str(spec.params.get("memory_backend", "list") or "list")
    )
    built = builder.build(model=model, memory_bank=memory_bank)
    return adapter.ConcordiaGameMasterAdapter(built)
