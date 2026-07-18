"""Helpers shared by the built-in panel modules."""

from __future__ import annotations

from typing import Any

from silisocs.evaluations.vocabulary import (
    EventSemantics,
    event_semantics_for,
    parse_event_semantics,
)


def _artifact_cache(artifact: Any) -> dict[Any, Any]:
    """Return a per-artifact memo dict for run-constant capability resolution.

    A backend's semantics are constant for the whole run, while the manifest
    fallback would otherwise reparse them for every row. Falls back to a
    throwaway dict if the artifact rejects the attribute.
    """
    cache = getattr(artifact, "_panel_capability_cache", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    try:
        object.__setattr__(artifact, "_panel_capability_cache", cache)
    except (AttributeError, TypeError):
        return {}
    return cache


def episode_of(row: dict[str, Any]) -> int:
    """Return an event row's episode index, or zero when invalid."""
    value = row.get("episode", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def backend_types(artifact: Any) -> list[str]:
    """Return the run's distinct backend types from its manifest GM layout."""
    seen: list[str] = []
    for game_master in getattr(artifact, "game_masters", []) or []:
        backend_type = game_master.get("backend_type") if isinstance(game_master, dict) else None
        if isinstance(backend_type, str) and backend_type not in seen:
            seen.append(backend_type)
    return seen


def backend_type_for_event(row: dict[str, Any], artifact: Any) -> str:
    """Return the backend type stamped on an event row."""
    del artifact
    return str(row.get("backend_type") or "").strip()


def _manifest_semantics(artifact: Any, backend_type: str) -> EventSemantics:
    """Read semantics recorded for a backend that is unavailable in this process."""
    for game_master in getattr(artifact, "game_masters", []) or []:
        if not isinstance(game_master, dict) or game_master.get("backend_type") != backend_type:
            continue
        semantics = parse_event_semantics(game_master.get("event_semantics"))
        if semantics.roles or semantics.fields or semantics.label_tags:
            return semantics
    return EventSemantics()


def semantics_for_backend(artifact: Any, backend_type: str) -> EventSemantics:
    """Resolve one backend's semantics: live declaration first, else the manifest."""
    if not backend_type:
        return EventSemantics()
    cache = _artifact_cache(artifact)
    cache_key = ("semantics", backend_type)
    if cache_key in cache:
        return cache[cache_key]
    registered = event_semantics_for(backend_type)
    result = (
        registered
        if (registered.roles or registered.fields or registered.label_tags)
        else _manifest_semantics(artifact, backend_type)
    )
    cache[cache_key] = result
    return result


def event_semantics_for_event(row: dict[str, Any], artifact: Any) -> EventSemantics:
    """Resolve optional event semantics for an event's backend."""
    return semantics_for_backend(artifact, backend_type_for_event(row, artifact))


def run_semantic_roles(artifact: Any) -> frozenset[str]:
    """Every semantic role the run's backends can actually populate.

    This is the run's capability set: a panel names the roles it needs and is
    shown only when some backend here declares one of them, which is how a market
    run never renders a follow graph and a social run never renders a ledger.
    """
    roles: set[str] = set()
    for backend_type in backend_types(artifact):
        semantics = semantics_for_backend(artifact, backend_type)
        roles.update(role for role, labels in semantics.roles.items() if labels)
    return frozenset(roles)


def run_has_tags(artifact: Any) -> bool:
    """Whether any backend in the run declares action tags or semantic roles."""
    return bool(run_semantic_roles(artifact))
