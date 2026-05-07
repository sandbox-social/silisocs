"""Resolve component for the EchoChamberSim replication."""

from __future__ import annotations

import json
import re
from typing import Any

from concordia.typing import entity as entity_lib
from concordia.typing import entity_component


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


class EchoChamberResolve(entity_component.ContextComponent, entity_component.ComponentWithLogging):
    """Parse agent belief updates and stage them in shared replication state."""

    def __init__(self, *, sm_app: Any | None = None, **kwargs: Any) -> None:
        del kwargs
        super().__init__()
        self._sm_app = sm_app

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        if action_spec.output_type != entity_lib.OutputType.RESOLVE:
            return ""
        if self._sm_app is None or not hasattr(self._sm_app, "echo_stage_update"):
            raise TypeError("EchoChamberResolve requires an app with echo_stage_update().")
        call = str(action_spec.call_to_action or "")
        if ":" not in call:
            return "Malformed echo chamber action."
        name, action_text = call.split(":", 1)
        active_entity = name.strip()
        payload = _extract_json(action_text) or {}
        episode = int(payload.get("episode", 0) or 0)
        self._sm_app.echo_stage_update(name=active_entity, episode=episode, update=payload)
        result = f"Staged echo chamber update for {active_entity} at episode {episode}."
        self._logging_channel(
            {
                "Key": "echo_chamber_resolve",
                "Summary": result,
                "Value": payload,
                "Active Entity": active_entity,
            }
        )
        return result

    def get_state(self) -> entity_component.ComponentState:
        return {}

    def set_state(self, state: entity_component.ComponentState) -> None:
        del state
