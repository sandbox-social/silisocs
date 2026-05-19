"""Agent initialization phase implementations."""

from __future__ import annotations

import functools
import importlib
import inspect
from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.initialization.context import AgentInitializationContext, InitializationContext
from silisocs.runtime.execution import concurrency
from silisocs.runtime.language_models import LanguageModel


class AgentInitializer:
    """Base class for engine-owned agent initialization."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        model: LanguageModel,
        context: InitializationContext,
    ) -> None:
        """Initialize all agents before game masters and simulation setup."""
        del agents, model, context


class DefaultAgentInitializer(AgentInitializer):
    """Call every agent's own initialize hook with a typed payload."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        model: LanguageModel,
        context: InitializationContext,
    ) -> None:
        del model

        def _init_agent(agent: Agent) -> None:
            payload = build_agent_initialization_context(agent.name, context)
            agent.initialize(payload)

        concurrency.run_tasks(
            {agent.name: functools.partial(_init_agent, agent) for agent in agents}
        )


class RawMemoryAgentInitializer(DefaultAgentInitializer):
    """Default agent initializer using configured raw memories."""


class NoOpAgentInitializer(AgentInitializer):
    """Agent initializer that intentionally does nothing."""


def build_agent_initialization_context(
    agent_name: str,
    context: InitializationContext,
    *,
    generated_memories: Sequence[str] = (),
) -> AgentInitializationContext:
    """Build the typed payload for one agent."""
    shared = tuple(_normalize_memories(context.shared_memories))
    specific = tuple(_normalize_memories(context.player_specific_memories.get(agent_name, ())))
    generated = tuple(_normalize_memories(generated_memories))
    memories = tuple(item for item in (*shared, *generated, *specific) if item)
    return AgentInitializationContext(
        agent_name=agent_name,
        memories=memories,
        shared_memories=shared,
        specific_memories=specific,
        player_context=str(context.player_specific_context.get(agent_name, "")),
        sim_role=str(context.sim_roles.get(agent_name, "")),
        flow_tag=str(context.agent_flow_tags.get(agent_name, "default") or "default"),
        bio=str(context.agent_bios.get(agent_name, "")),
    )


def _normalize_memories(memories: object) -> list[str]:
    if memories is None:
        return []
    if isinstance(memories, str):
        lines = [line.strip() for line in memories.splitlines() if line.strip()]
        return lines or ([memories.strip()] if memories.strip() else [])
    if isinstance(memories, (list, tuple)):
        return [str(item).strip() for item in memories if str(item).strip()]
    return [str(memories).strip()] if str(memories).strip() else []


def build_agent_initializer(slot_cfg: Mapping[str, Any] | None = None) -> AgentInitializer:
    """Build an agent initializer from ``sim.initialization.agents`` config."""
    cfg = dict(slot_cfg or {})
    class_path = str(cfg.get("class_path") or "").strip()
    params = dict(cfg.get("params") or {})
    if class_path:
        initializer = _instantiate_with_supported_kwargs(_load_class(class_path), params)
    else:
        built_in = str(cfg.get("built_in") or "default").strip()
        from silisocs.initialization.agents.formative import FormativeAgentInitializer

        built_ins: dict[str, type[AgentInitializer]] = {
            "default": DefaultAgentInitializer,
            "raw_memory": RawMemoryAgentInitializer,
            "formative_memory": FormativeAgentInitializer,
            "none": NoOpAgentInitializer,
            "disabled": NoOpAgentInitializer,
        }
        if built_in not in built_ins:
            options = ", ".join(sorted(built_ins))
            raise ValueError(
                f"Unknown sim.initialization.agents built_in '{built_in}'. Available: {options}"
            )
        initializer = _instantiate_with_supported_kwargs(built_ins[built_in], params)
    if not isinstance(initializer, AgentInitializer):
        raise TypeError(
            "Agent initializer must subclass "
            "silisocs.initialization.agents.runtime.AgentInitializer"
        )
    return initializer


def _load_class(class_path: str) -> type[Any]:
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _instantiate_with_supported_kwargs(cls: type[Any], kwargs: Mapping[str, Any]) -> Any:
    params = inspect.signature(cls.__init__).parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return cls(**dict(kwargs))
    supported = {
        name
        for name, param in params.items()
        if name != "self"
        and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    unsupported = sorted(set(kwargs) - supported)
    if unsupported:
        raise ValueError(
            f"Unsupported config param(s) for {cls.__module__}.{cls.__name__}: "
            f"{unsupported}. Supported params: {sorted(supported)}"
        )
    return cls(**{key: value for key, value in kwargs.items() if key in supported})
