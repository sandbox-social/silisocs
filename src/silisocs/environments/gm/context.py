"""Runtime context passed to native game-master components."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GameMasterContext:
    """Small dependency bundle for game-master components."""

    gm_name: str
    backend: Any
    agents: Sequence[Any]
    agent_names: tuple[str, ...]
    agent_flow_tags: Mapping[str, str]
    model: Any | None = None
    event_logger: Any | None = None
