"""Resolve component for the EchoChamberSim replication."""

from __future__ import annotations

import json
import re
from typing import Any

from silisocs.environments.gm.components.base import BaseComponent
from silisocs.runtime.types import ActionOutput


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    # BaseRuntimeEngine escapes model JSON braces before formatting the resolve
    # call-to-action. Undo that protection here for component-owned JSON.
    if "{{" in raw or "}}" in raw:
        candidates.append(raw.replace("{{", "{").replace("}}", "}"))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not match:
            continue
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else None
    return None


class EchoChamberResolve(BaseComponent):
    """Parse agent belief updates and stage them in shared replication state."""

    def __init__(self, *, backend: Any | None = None, **kwargs: Any) -> None:
        del kwargs
        super().__init__()
        self._backend = backend

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        if self._backend is None or not hasattr(self._backend, "echo_stage_update"):
            raise TypeError("EchoChamberResolve requires an app with echo_stage_update().")
        active_agent = str(agent_name).strip()
        action_text = action.text if isinstance(action, ActionOutput) else str(action or "")
        payload = _extract_json(action_text) or {}
        episode = int(payload.get("episode", 0) or 0)
        self._backend.echo_stage_update(name=active_agent, episode=episode, update=payload)
        result = f"Staged echo chamber update for {active_agent} at episode {episode}."
        return result

    def get_state(self) -> dict[str, Any]:
        return {}

    def set_state(self, state: dict[str, Any]) -> None:
        del state
