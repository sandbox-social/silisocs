"""Initializer component for the EchoChamberSim replication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.environments.gm.components.base import BackendInitializer


class EchoChamberInitializer(BackendInitializer):
    """Initialize the social app and validate its EchoChamberSim state."""

    def initialize(
        self,
        *,
        sm_app: Any,
        agent_names: Sequence[str],
        init_kwargs: Mapping[str, Any],
    ) -> None:
        sm_app.initialize(list(agent_names), **dict(init_kwargs or {}))
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
