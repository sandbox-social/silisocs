"""No-op recommendation component for non-social echo chamber runs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from silisocs.environments.gm.components.base import UpdateComponent


class NoOpUpdate(UpdateComponent):
    """Avoid social-backend recommendation work for this replication."""

    def __init__(self, **kwargs: Any) -> None:
        del kwargs
        super().__init__()

    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        del step, agents, context

    def get_state(self) -> dict[str, Any]:
        return {}

    def set_state(self, state: dict[str, Any]) -> None:
        del state
