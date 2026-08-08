"""Run-health counters, plus the documented import home of event semantics.

Event semantics — the open action tags and semantic fields a backend uses to
describe its own actions — live in the environments layer
(:mod:`silisocs.environments.backends.event_semantics`) because backends DECLARE
them; they are re-exported here for readers above that layer, so the dependency
stays one-directional (evaluations/analysis -> environments).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from silisocs.environments.backends.event_semantics import (
    EventSemantics,
    declared_event_semantics,
    derive_event_semantics,
    event_semantics_for,
    merge_event_semantics,
    parse_event_semantics,
    register_event_semantics,
    social_event_semantics,
)

# Degraded-run counters, in one place: incremented during the run through
# ``SimMetricsCollector.increment_counter``, warned about at run end
# (``session._warn_degraded_health``), written to ``run_manifest.json`` under
# ``health``, and read back by ``RunArtifact.health``. Registering a counter here is
# what makes it visible on all of those surfaces.
#
# A counter records something the run SURVIVED — an isolated agent turn, one failed
# tool call, one routing call that fell back. Anything a run cannot legitimately
# continue past raises instead of being counted.
_HEALTH_COUNTER_REGISTRY: dict[str, str] = {
    "agent_turn_failures": "agent turns raised an exception",
    "action_parse_failures": "agent actions were dropped as unparseable",
    "action_invalid_targets": "agent actions referenced invalid target ids",
    "backend_action_errors": "backend actions raised unexpected exceptions",
    "exposure_log_failures": "exposure rows could not be written to exposure_events.jsonl",
    "harness_tool_failures": "harness tool calls failed",
    "recsys_update_failures": "scheduled recommendation updates failed",
    "routing_fallbacks": "branch routing calls fell back to a default choice",
}
# A LIVE read-only view: registrations made before a run's end are visible to
# every consumer (degraded warning, manifest health block, RunArtifact.health)
# without any of them re-importing. Consumers must iterate this at use time,
# never snapshot it at import.
HEALTH_COUNTERS: Mapping[str, str] = MappingProxyType(_HEALTH_COUNTER_REGISTRY)


def register_health_counter(name: str, description: str) -> None:
    """Register a custom degraded-run counter (mirrors ``register_event_semantics``).

    A registered counter joins the built-ins on every health surface: increment
    it with ``SimMetricsCollector.get().increment_counter(name)`` and it shows
    up in the run-end degraded warning, ``run_manifest.json``'s ``health``
    block, and ``RunArtifact.health``. Re-registering an existing name with a
    different description is a conflict and raises.
    """
    key = str(name).strip()
    if not key:
        raise ValueError("Health counter name must be a non-empty string.")
    existing = _HEALTH_COUNTER_REGISTRY.get(key)
    if existing is not None and existing != description:
        raise ValueError(
            f"Health counter {key!r} is already registered as {existing!r}; "
            "pick a distinct name for a distinct signal."
        )
    _HEALTH_COUNTER_REGISTRY[key] = str(description)


__all__ = [
    "HEALTH_COUNTERS",
    "EventSemantics",
    "declared_event_semantics",
    "derive_event_semantics",
    "event_semantics_for",
    "merge_event_semantics",
    "parse_event_semantics",
    "register_event_semantics",
    "register_health_counter",
    "social_event_semantics",
]
