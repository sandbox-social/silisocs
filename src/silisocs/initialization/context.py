"""Shared startup context for engine-owned initialization phases."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any


@dataclasses.dataclass(frozen=True)
class InitializationContext:
    """Inputs available during engine startup initialization."""

    shared_memories: tuple[str, ...] = ()
    player_specific_memories: Mapping[str, tuple[str, ...]] = dataclasses.field(
        default_factory=dict
    )
    player_specific_context: Mapping[str, str] = dataclasses.field(default_factory=dict)
    sim_roles: Mapping[str, str] = dataclasses.field(default_factory=dict)
    agent_flow_tags: Mapping[str, str] = dataclasses.field(default_factory=dict)
    agent_bios: Mapping[str, str] = dataclasses.field(default_factory=dict)
    checkpoint: Mapping[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class AgentInitializationContext:
    """Typed payload passed to one agent's ``initialize`` method."""

    agent_name: str
    memories: tuple[str, ...] = ()
    shared_memories: tuple[str, ...] = ()
    specific_memories: tuple[str, ...] = ()
    player_context: str = ""
    sim_role: str = ""
    flow_tag: str = "default"
    bio: str = ""
