"""Initializer component for the EchoChamberSim replication."""

from __future__ import annotations

from typing import Any

from silisocs.initialization.game_masters import GameMasterInitializer


class EchoChamberInitializer(GameMasterInitializer):
    """Initialize the social app and validate its EchoChamberSim state."""

    def initialize(
        self,
        *,
        agents: Any,
        game_master: Any,
        context: Any,
    ) -> None:
        agent_names = [agent.name for agent in agents]
        sm_app = getattr(game_master, "app", getattr(game_master, "sm_app", None))
        if sm_app is None:
            raise TypeError("EchoChamberInitializer requires a game master with an app.")
        sm_app.initialize(
            list(agent_names),
            sim_roles=dict(getattr(context, "sim_roles", {}) or {}),
            social_network=dict(getattr(context, "social_network", {}) or {}),
            seed_posts={},
        )
        self._validate(sm_app, agent_names)

    @staticmethod
    def _validate(sm_app: Any, agent_names: list[str]) -> None:
        state = getattr(sm_app, "echo_state", None)
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
