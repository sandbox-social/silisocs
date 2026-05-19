"""Build initialization context from runtime specs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from omegaconf import DictConfig, OmegaConf

from silisocs.initialization.context import InitializationContext
from silisocs.runtime.construction.game_masters import DEFAULT_FLOW_TAG
from silisocs.runtime.construction.specs import RuntimeSpec


def _env_cfg(cfg: Any) -> Any:
    return getattr(cfg, "env", getattr(cfg, "environment", object()))


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


def _to_plain_container(value: Any) -> Any:
    if OmegaConf.is_config(value):
        return OmegaConf.to_container(value, resolve=True)
    return value


def build_initializer_context(
    cfg: DictConfig,
    agent_configs: Sequence[RuntimeSpec],
) -> InitializationContext:
    scenario_shared = OmegaConf.select(cfg, "agents.shared_memories")
    if scenario_shared is None:
        scenario_shared = OmegaConf.select(cfg, "agents.persona_pipeline.defaults.shared_memories")

    shared_memories = _normalize_memories(scenario_shared)
    usage = str(getattr(_env_cfg(cfg), "usage_instructions", "") or "").strip()
    if usage:
        shared_memories.append(usage)

    player_specific_memories: dict[str, tuple[str, ...]] = {}
    player_specific_context: dict[str, str] = {}
    sim_roles: dict[str, str] = {}
    agent_flow_tags: dict[str, str] = {}
    agent_bios: dict[str, str] = {}
    for agent in agent_configs:
        agent_name = str(agent.params["name"])
        player_specific_memories[agent_name] = tuple(
            _normalize_memories(agent.params.get("specific_memories", []))
        )
        player_specific_context[agent_name] = str(agent.params.get("context", ""))
        sim_role = agent.params.get("sim_role", {})
        if isinstance(sim_role, Mapping):
            sim_roles[agent_name] = str(sim_role.get("name", ""))
        flow_tag = str(agent.params.get("flow_tag", DEFAULT_FLOW_TAG) or DEFAULT_FLOW_TAG).strip()
        agent_flow_tags[agent_name] = flow_tag or DEFAULT_FLOW_TAG
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
        social_network=cast(
            Mapping[str, Any],
            _to_plain_container(OmegaConf.select(cfg, "env.social_network", default={}) or {}),
        ),
        agent_bios=agent_bios,
    )


def populate_agent_data(
    agent_configs: Sequence[RuntimeSpec],
    game_masters: Sequence[RuntimeSpec],
) -> None:
    sim_roles: dict[str, str] = {}
    agent_flow_tags: dict[str, str] = {}

    for agent in agent_configs:
        agent_name = agent.params["name"]
        sim_roles[agent_name] = agent.params["sim_role"]["name"]
        flow_tag = str(agent.params.get("flow_tag", DEFAULT_FLOW_TAG) or DEFAULT_FLOW_TAG).strip()
        agent_flow_tags[agent_name] = flow_tag or DEFAULT_FLOW_TAG

    social_media_gms = list(game_masters)
    if not social_media_gms:
        raise ValueError("No environment game master found.")
    for social_media_gm in social_media_gms:
        user_data = social_media_gm.params.setdefault(
            "environment_data",
            social_media_gm.params.setdefault("sm_user_data", {}),
        )
        social_media_gm.params["sm_user_data"] = user_data
        user_data.setdefault("sim_roles", {}).update(sim_roles)
        user_data["agent_flow_tags"] = dict(agent_flow_tags)

        orchestration = user_data.setdefault("gm_orchestration", {})
        owned_flows_raw = (
            orchestration.get("owned_flows", []) if isinstance(orchestration, dict) else []
        )
        owned_flows = {str(flow).strip() for flow in owned_flows_raw if str(flow).strip()}
        if isinstance(orchestration, dict) and owned_flows:
            orchestration["owned_entities"] = sorted(
                name for name, flow in agent_flow_tags.items() if flow in owned_flows
            )
