"""Initializer component for the EchoChamberSim replication."""

from __future__ import annotations

from typing import Any

from silisocs.initialization.game_masters import GameMasterInitializer


class EchoChamberInitializer(GameMasterInitializer):
    """Initialize the social app and validate its EchoChamberSim state."""

    def __init__(self, graph: dict[str, Any] | None = None) -> None:
        self.graph = dict(graph or {})

    def initialize(
        self,
        *,
        agents: Any,
        gm_context: Any,
        context: Any,
    ) -> None:
        agent_names = [agent.name for agent in agents]
        backend = getattr(gm_context, "backend", None)
        if backend is None:
            raise TypeError("EchoChamberInitializer requires a game-master context with a backend.")
        backend.initialize(
            list(agent_names),
            sim_roles=dict(getattr(context, "sim_roles", {}) or {}),
            graph_config=self.graph,
            seed_posts={},
        )
        self._validate(backend, agent_names)

    @staticmethod
    def _validate(backend: Any, agent_names: list[str]) -> None:
        state = getattr(backend, "echo_state", None)
        if state is None:
            raise ValueError("EchoChamberInitializer requires an app with echo_state.")
        expected = set(state.agent_names)
        actual = set(str(name) for name in agent_names)
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing or extra:
            raise ValueError(
                "EchoChamber agent mismatch. "
                f"missing_from_runtime={missing[:5]} extra_runtime={extra[:5]}"
            )
