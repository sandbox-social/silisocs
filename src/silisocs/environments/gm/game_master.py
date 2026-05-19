"""Default native game master."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from silisocs.environments.gm.base_game_master import (
    EnvironmentGameMaster,
    _GameMasterWiring,
)


class GameMaster(EnvironmentGameMaster):
    """Single-flow native game master.

    This public class is the runtime object consumed by the engine. The
    Component wiring is internal; callers instantiate ``GameMaster`` directly
    from config and must not call a separate ``build()`` step.
    """

    def __init__(
        self,
        *,
        model: Any | None = None,
        agents: Sequence[Any] = (),
        **params: Any,
    ) -> None:
        runtime_kwargs = _GameMasterWiring(
            model=model,
            agents=agents,
            **params,
        ).build_runtime_kwargs(model=model)
        super().__init__(**runtime_kwargs)
