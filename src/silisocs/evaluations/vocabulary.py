"""Open action tags, semantic fields, and run-health counters shared by analysis
surfaces.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

# Degraded-run counters, in one place: incremented during the run through
# ``SimMetricsCollector.increment_counter``, warned about at run end
# (``session._warn_degraded_health``), written to ``run_manifest.json`` under
# ``health``, and read back by ``RunArtifact.health``. Registering a counter here is
# what makes it visible on all of those surfaces.
#
# A counter records something the run SURVIVED — an isolated agent turn, one failed
# tool call, one routing call that fell back. Anything a run cannot legitimately
# continue past raises instead of being counted.
HEALTH_COUNTERS: Mapping[str, str] = MappingProxyType(
    {
        "agent_turn_failures": "agent turns raised an exception",
        "action_parse_failures": "agent actions were dropped as unparseable",
        "action_invalid_targets": "agent actions referenced invalid target ids",
        "backend_action_errors": "backend actions raised unexpected exceptions",
        "harness_tool_failures": "harness tool calls failed",
        "routing_fallbacks": "branch routing calls fell back to a default choice",
    }
)


@dataclass(frozen=True)
class EventSemantics:
    """Open tags and payload paths for normalized action-event analysis.

    ``roles`` maps any semantic name to the labels that provide it. ``label_tags``
    preserves each label's declared tag order, with the first tag acting as its
    primary analysis bucket. ``fields`` maps semantic field names to fallback
    dotted paths in logged event data.
    """

    roles: Mapping[str, frozenset[str]] = field(default_factory=dict)
    fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    label_tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "roles",
            MappingProxyType({str(key): frozenset(value) for key, value in self.roles.items()}),
        )
        object.__setattr__(
            self,
            "fields",
            MappingProxyType({str(key): tuple(value) for key, value in self.fields.items()}),
        )
        object.__setattr__(
            self,
            "label_tags",
            MappingProxyType(
                {str(key): tuple(dict.fromkeys(value)) for key, value in self.label_tags.items()}
            ),
        )

    def labels(self, role: str) -> frozenset[str]:
        """Return labels registered for a semantic role or tag."""
        return self.roles.get(role, frozenset())

    def tags_of(self, label: str) -> tuple[str, ...]:
        """Return a label's ordered tags, falling back to role membership."""
        explicit = self.label_tags.get(str(label))
        if explicit is not None:
            return explicit
        return tuple(role for role, labels in self.roles.items() if label in labels)

    def value(self, data: Mapping[str, Any], field_name: str) -> Any:
        """Read the first populated dotted payload path for a semantic field."""
        for path in self.fields.get(field_name, ()):
            value: Any = data
            for part in path.split("."):
                if not isinstance(value, Mapping) or part not in value:
                    value = None
                    break
                value = value[part]
            if value not in (None, ""):
                return value
        return None


_EVENT_SEMANTICS: dict[str, EventSemantics] = {}
_RESOLVED_EVENT_SEMANTICS: dict[str, EventSemantics] = {}


def _backend_class(backend_type: str) -> type[Any] | None:
    """Resolve a built-in or already-constructed custom backend class."""
    key = str(backend_type).strip()
    if not key:
        return None
    try:
        from silisocs.environments.backends.factory import (
            registered_backend_types,
            resolve_backend_class,
            runtime_backend_class,
        )

        if key in registered_backend_types():
            return resolve_backend_class(key)
        return runtime_backend_class(key)
    except Exception:
        # Artifact readers may not have the run's custom backend installed.
        return None


def register_event_semantics(backend_type: str, semantics: EventSemantics) -> None:
    """Register or replace the semantics for a backend type."""
    key = str(backend_type).strip()
    if not key:
        raise ValueError("backend_type must be non-empty")
    if not isinstance(semantics, EventSemantics):
        raise TypeError("semantics must be EventSemantics")
    _EVENT_SEMANTICS[key] = semantics
    _RESOLVED_EVENT_SEMANTICS.pop(key, None)


def parse_event_semantics(raw: Any) -> EventSemantics:
    """Build semantics from the portable ``{roles, fields, labels}`` shape.

    Deliberately lenient: this parses ARTIFACT data (run manifests written by
    other versions), where a malformed block degrades to empty semantics rather
    than breaking every analysis surface. Author-owned class declarations go
    through the strict :func:`declared_event_semantics` instead.
    """
    if isinstance(raw, EventSemantics):
        return raw
    if not isinstance(raw, Mapping):
        return EventSemantics()
    roles = raw.get("roles") or {}
    fields = raw.get("fields") or {}
    labels = raw.get("labels") or {}
    if not all(isinstance(value, Mapping) for value in (roles, fields, labels)):
        return EventSemantics()
    return EventSemantics(roles=roles, fields=fields, label_tags=labels)


def declared_event_semantics(cls: type[Any]) -> EventSemantics | None:
    """Parse a backend class's ``event_semantics`` declaration, failing loud.

    Unlike manifest parsing (untrusted data, best-effort), a class-level
    declaration is the author's own code: a malformed shape is a bug, and
    silently degrading it to empty semantics would just make every panel
    quietly disappear. Accepts an :class:`EventSemantics` instance or the
    portable ``{roles, fields, labels}`` mapping.
    """
    raw = getattr(cls, "event_semantics", None)
    if raw is None or (isinstance(raw, Mapping) and not raw):
        return None
    if isinstance(raw, EventSemantics):
        return raw
    if not isinstance(raw, Mapping):
        raise TypeError(
            f"{cls.__name__}.event_semantics must be an EventSemantics or a "
            f"{{roles, fields, labels}} mapping, got {type(raw).__name__}"
        )
    parsed = parse_event_semantics(raw)
    if not (parsed.roles or parsed.fields or parsed.label_tags):
        raise ValueError(
            f"{cls.__name__}.event_semantics is malformed: 'roles', 'fields', and "
            "'labels' must each be mappings when present."
        )
    return parsed


def merge_event_semantics(declared: EventSemantics, derived: EventSemantics) -> EventSemantics:
    """Merge an explicit declaration with decorator-derived semantics.

    The declaration wins per entry; derived declarations fill everything they
    add — so decorating a NEW action on a backend that also carries an explicit
    declaration extends its semantics instead of being silently ignored.
    Roles union their label sets; a field's declared paths come first with
    unseen derived paths appended; a label declared in both keeps its declared
    tag order.
    """
    if not (derived.roles or derived.fields or derived.label_tags):
        return declared
    if not (declared.roles or declared.fields or declared.label_tags):
        return derived
    merged_roles: dict[str, set[str]] = {
        name: set(labels) for name, labels in derived.roles.items()
    }
    for name, labels in declared.roles.items():
        merged_roles.setdefault(name, set()).update(labels)
    roles = {name: frozenset(labels) for name, labels in merged_roles.items()}
    fields = {
        name: tuple(dict.fromkeys((*declared.fields.get(name, ()), *derived.fields.get(name, ()))))
        for name in {**derived.fields, **declared.fields}
    }
    label_tags = {**derived.label_tags, **declared.label_tags}
    return EventSemantics(roles=roles, fields=fields, label_tags=label_tags)


def derive_event_semantics(cls: type[Any]) -> EventSemantics:
    """Derive tags and fields from a backend's decorated action catalog."""
    catalog = getattr(cls, "declared_action_catalog", None)
    if not callable(catalog):
        return EventSemantics()

    roles: dict[str, set[str]] = {}
    fields: dict[str, list[str]] = {}
    label_tags: dict[str, tuple[str, ...]] = {}
    for action in catalog():
        if not action.get("log", True):
            continue
        label = str(action.get("log_as") or action.get("name") or "").strip()
        if not label:
            continue
        tags = tuple(dict.fromkeys(str(tag) for tag in action.get("tags", ()) if str(tag)))
        if tags:
            label_tags[label] = tuple(dict.fromkeys((*label_tags.get(label, ()), *tags)))
        for tag in tags:
            roles.setdefault(tag, set()).add(label)
        for name, paths in dict(action.get("fields") or {}).items():
            ordered = fields.setdefault(str(name), [])
            for raw_path in paths:
                path = str(raw_path)
                if path and path not in ordered:
                    ordered.append(path)
    return EventSemantics(
        roles={name: frozenset(labels) for name, labels in roles.items()},
        fields={name: tuple(paths) for name, paths in fields.items()},
        label_tags=label_tags,
    )


def event_semantics_for(backend_type: str) -> EventSemantics:
    """Resolve a backend type's semantics.

    An explicit ``register_event_semantics`` registration or class-level
    ``event_semantics`` declaration is the base (registration wins over the
    class attribute), and decorator-derived declarations are MERGED in — so
    ``@app_action(tags=...)`` on a new action always reaches the analysis
    surfaces, even for a backend that carries an explicit declaration.
    """
    key = str(backend_type).strip()
    cached = _RESOLVED_EVENT_SEMANTICS.get(key)
    if cached is not None:
        return cached

    cls = _backend_class(key)
    declared = _EVENT_SEMANTICS.get(key)
    if declared is None and cls is not None:
        declared = declared_event_semantics(cls)
    declared = declared or EventSemantics()
    derived = derive_event_semantics(cls) if cls is not None else EventSemantics()
    semantics = merge_event_semantics(declared, derived)
    if cls is None and not (declared.roles or declared.fields or declared.label_tags):
        # Nothing known yet; don't cache so a later-registered runtime class
        # (or registration) is picked up on the next call.
        return semantics
    _RESOLVED_EVENT_SEMANTICS[key] = semantics
    return semantics


def social_event_semantics(
    *,
    roots: set[str],
    replies: set[str],
    reactions: set[str],
    follows: set[str],
    negative: set[str],
    reads: set[str],
    content_ids: tuple[str, ...],
    response_ids: tuple[str, ...],
    parent_ids: tuple[str, ...],
    reaction_target_id_fields: tuple[str, ...] = (),
    reaction_target_type_fields: tuple[str, ...] = (),
) -> EventSemantics:
    """Build the shipped social capability mappings using ordinary open tags."""
    conventional = {
        "creates_content": roots | replies,
        "endorses": reactions - negative,
        "negative": negative,
        "social_graph": follows,
        "reads": reads,
    }
    roles: dict[str, frozenset[str]] = {
        "content.root": frozenset(roots),
        "content.reply": frozenset(replies),
        "interaction.reaction": frozenset(reactions),
        "network.follow": frozenset(follows),
        **{tag: frozenset(labels) for tag, labels in conventional.items()},
    }
    label_tags: dict[str, tuple[str, ...]] = {}
    for tag, action_labels in conventional.items():
        for label in action_labels:
            label_tags[label] = (tag,)
    for role, registered_labels in roles.items():
        for label in registered_labels:
            label_tags[label] = tuple(dict.fromkeys((*label_tags.get(label, ()), role)))
    return EventSemantics(
        roles=roles,
        fields={
            "content.id": content_ids,
            "content.response_id": response_ids,
            "content.parent_id": parent_ids,
            "content.text": ("post_text", "content", "text", "title"),
            "network.target_actor": ("target_user", "target_username"),
            "interaction.target_id": reaction_target_id_fields,
            "interaction.target_type": reaction_target_type_fields,
        },
        label_tags=label_tags,
    )


# The shipped social backends declare their own semantics: each app class sets
# `event_semantics = social_event_semantics(...)` — the same self-description
# seam custom backends use — so forking one and decorating a new action extends
# its semantics (via merge_event_semantics) without editing any central table.
