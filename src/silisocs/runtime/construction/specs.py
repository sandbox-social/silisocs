"""Native runtime configuration records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RuntimeRole(str, Enum):
    """Role of a configured runtime object."""

    AGENT = "agent"
    GAME_MASTER = "game_master"


@dataclass(frozen=True)
class SimRole:
    """Scenario role metadata attached to an agent or game master."""

    name: str
    module_path: str = ""


@dataclass
class RuntimeSpec:
    """Direct native runtime object specification."""

    class_path: str
    role: RuntimeRole | str
    params: dict[str, Any] = field(default_factory=dict)
    compat: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, RuntimeRole):
            self.role = RuntimeRole(str(self.role))
        self.params = dict(self.params or {})
        compat = str(self.compat or self.params.pop("compat", "") or "").strip().lower()
        self.compat = compat or None


@dataclass
class AgentConfig(RuntimeSpec):
    """Runtime spec for one native or compatibility agent."""

    class_path: str = ""
    role: RuntimeRole = RuntimeRole.AGENT


@dataclass
class GameMasterConfig(RuntimeSpec):
    """Runtime spec for one native or compatibility game master."""

    class_path: str = ""
    role: RuntimeRole = RuntimeRole.GAME_MASTER
