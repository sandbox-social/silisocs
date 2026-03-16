"""Backend initializer hooks for social-media game masters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mastodon_sim.environments.gm.components.base import BackendInitializer


@dataclass
class DefaultBackendInitializer(BackendInitializer):
    """Default backend initialization hook.

    Passes all precomputed init kwargs to sm_app.initialize().
    """

    def initialize(
        self,
        *,
        sm_app: Any,
        agent_names: Sequence[str],
        init_kwargs: Mapping[str, Any],
    ) -> None:
        kwargs = dict(init_kwargs)
        sm_app.initialize(agent_names=list(agent_names), **kwargs)
