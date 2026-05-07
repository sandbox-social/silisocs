"""No-op recommendation component for non-social echo chamber runs."""

from __future__ import annotations

from typing import Any

from concordia.typing import entity as entity_lib
from concordia.typing import entity_component


class NoOpRecommendation(entity_component.ContextComponent, entity_component.ComponentWithLogging):
    """Avoid social-backend recommendation work for this replication."""

    def __init__(self, **kwargs: Any) -> None:
        del kwargs
        super().__init__()

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        del action_spec
        return ""

    def post_act(self, action_attempt: str) -> str:
        del action_attempt
        return ""

    def update(self) -> None:
        return None

    def get_state(self) -> entity_component.ComponentState:
        return {}

    def set_state(self, state: entity_component.ComponentState) -> None:
        del state
