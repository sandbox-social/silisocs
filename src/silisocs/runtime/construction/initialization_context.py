"""Build initialization context from runtime specs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from omegaconf import DictConfig, OmegaConf

from silisocs.initialization.context import InitializationContext
from silisocs.runtime.construction.game_masters import DEFAULT_FLOW_TAG
from silisocs.runtime.construction.specs import RuntimeSpec


def _normalize_memories(memories: Any) -> list[str]:
    if memories is None:
        return []
    if isinstance(memories, str):
        lines = [line.strip() for line in memories.splitlines() if line.strip()]
        if lines:
            return lines
        return [memories.strip()] if memories.strip() else []
    if isinstance(memories, list):
        return [str(item).strip() for item in memories if str(item).strip()]
    return [str(memories).strip()] if str(memories).strip() else []


def build_initializer_context(
    cfg: DictConfig,
    agent_configs: Sequence[RuntimeSpec],
) -> InitializationContext:
    shared_memories_cfg = OmegaConf.select(cfg, "agents.shared_memories")
    if shared_memories_cfg is None:
        shared_memories_cfg = OmegaConf.select(
            cfg, "agents.persona_pipeline.defaults.shared_memories"
        )

    shared_memories = _normalize_memories(shared_memories_cfg)
    player_specific_memories: dict[str, tuple[str, ...]] = {}
    player_specific_context: dict[str, str] = {}
    sim_roles: dict[str, str] = {}
    agent_flow_tags: dict[str, str] = {}
    agent_bios: dict[str, str] = {}
    final_flow_tags = _final_agent_flow_tags(cfg, agent_configs)
    for agent in agent_configs:
        agent_name = str(agent.params["name"])
        player_specific_memories[agent_name] = tuple(
            _normalize_memories(agent.params.get("specific_memories", []))
        )
        player_specific_context[agent_name] = str(agent.params.get("context", ""))
        sim_role = agent.params.get("sim_role", {})
        if isinstance(sim_role, Mapping):
            sim_roles[agent_name] = str(sim_role.get("name", ""))
        agent_flow_tags[agent_name] = final_flow_tags[agent_name]
        agent_bios[agent_name] = str(agent.params.get("bio", ""))
        for memory in _normalize_memories(agent.params.get("shared_memories", [])):
            if memory not in shared_memories:
                shared_memories.append(memory)

    return InitializationContext(
        shared_memories=tuple(shared_memories),
        player_specific_memories=player_specific_memories,
        player_specific_context=player_specific_context,
        sim_roles=sim_roles,
        agent_flow_tags=agent_flow_tags,
        agent_bios=agent_bios,
    )


def _final_agent_flow_tags(
    cfg: DictConfig,
    agent_configs: Sequence[RuntimeSpec],
) -> dict[str, str]:
    tags: dict[str, str] = {}
    for agent in agent_configs:
        agent_name = str(agent.params["name"])
        flow_tag = str(agent.params.get("flow_tag", DEFAULT_FLOW_TAG) or DEFAULT_FLOW_TAG).strip()
        tags[agent_name] = flow_tag or DEFAULT_FLOW_TAG

    raw_overrides = OmegaConf.select(cfg, "sim.engine.step.params.agent_to_flow", default={})
    if raw_overrides is None:
        return tags
    if not isinstance(raw_overrides, Mapping):
        raise ValueError("sim.engine.step.params.agent_to_flow must be a mapping.")

    unknown: list[str] = []
    for raw_name, raw_flow in raw_overrides.items():
        agent_name = str(raw_name).strip()
        if not agent_name:
            raise ValueError("sim.engine.step.params.agent_to_flow contains an empty agent name.")
        if agent_name not in tags:
            unknown.append(agent_name)
            continue
        tags[agent_name] = str(raw_flow or DEFAULT_FLOW_TAG).strip() or DEFAULT_FLOW_TAG
    if unknown:
        raise ValueError(
            f"sim.engine.step.params.agent_to_flow references unknown agent(s): {sorted(unknown)}"
        )
    return tags


def populate_agent_data(
    cfg: DictConfig,
    agent_configs: Sequence[RuntimeSpec],
    game_masters: Sequence[RuntimeSpec],
) -> None:
    sim_roles: dict[str, str] = {}
    agent_flow_tags = _final_agent_flow_tags(cfg, agent_configs)

    for agent in agent_configs:
        agent_name = agent.params["name"]
        sim_roles[agent_name] = agent.params["sim_role"]["name"]

    configured_gms = list(game_masters)
    if not configured_gms:
        raise ValueError("No environment game master found.")
    for game_master in configured_gms:
        game_master.params["sim_roles"] = dict(sim_roles)
        game_master.params["agent_flow_tags"] = dict(agent_flow_tags)
