"""Helpers shared by the built-in panel modules."""

from __future__ import annotations

from typing import Any

from silisocs.evaluations.vocabulary import (
    ActionVocabulary,
    EventSemantics,
    event_semantics_for,
    infer_event_semantics,
    vocabulary_for,
)


def episode_of(row: dict[str, Any]) -> int:
    """Best-effort episode index of an event row (probe rows may carry ``step``)."""
    value = row.get("episode", row.get("step", 0))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def backend_types(artifact: Any) -> list[str]:
    """The run's backend types from its manifest GM layout (may be empty for legacy runs)."""
    seen: list[str] = []
    for game_master in getattr(artifact, "game_masters", []) or []:
        backend_type = game_master.get("backend_type") if isinstance(game_master, dict) else None
        if isinstance(backend_type, str) and backend_type not in seen:
            seen.append(backend_type)
    return seen


def backend_type_for_event(row: dict[str, Any], artifact: Any) -> str:
    """Resolve an event's backend type, including legacy single-GM runs."""
    backend_type = str(row.get("backend_type") or "").strip()
    if not backend_type:
        types = backend_types(artifact)
        backend_type = types[0] if len(types) == 1 else ""
    return backend_type


def vocabulary_for_event(row: dict[str, Any], artifact: Any) -> ActionVocabulary:
    """Resolve an event vocabulary, including mixed-backend multi-GM runs."""
    return vocabulary_for(backend_type_for_event(row, artifact))


def event_semantics_for_event(row: dict[str, Any], artifact: Any) -> EventSemantics:
    """Resolve optional event semantics for an event's backend."""
    backend_type = backend_type_for_event(row, artifact)
    if backend_type:
        registered = event_semantics_for(backend_type)
        if registered.roles or registered.fields:
            return registered
        for game_master in getattr(artifact, "game_masters", []) or []:
            if not isinstance(game_master, dict) or game_master.get("backend_type") != backend_type:
                continue
            raw = game_master.get("event_semantics")
            if isinstance(raw, dict):
                roles = raw.get("roles") or {}
                fields = raw.get("fields") or {}
                if isinstance(roles, dict) and isinstance(fields, dict):
                    return EventSemantics(roles=roles, fields=fields)
        return registered
    return infer_event_semantics(str(row.get("label") or ""))
