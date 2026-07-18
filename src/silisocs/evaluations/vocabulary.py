"""Open action tags and semantic fields shared by analysis surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


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
_DERIVED_EVENT_SEMANTICS: dict[str, EventSemantics] = {}


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
    _DERIVED_EVENT_SEMANTICS.pop(key, None)


def parse_event_semantics(raw: Any) -> EventSemantics:
    """Build semantics from the portable ``{roles, fields, labels}`` shape."""
    if not isinstance(raw, Mapping):
        return EventSemantics()
    roles = raw.get("roles") or {}
    fields = raw.get("fields") or {}
    labels = raw.get("labels") or {}
    if not all(isinstance(value, Mapping) for value in (roles, fields, labels)):
        return EventSemantics()
    return EventSemantics(roles=roles, fields=fields, label_tags=labels)


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
    """Resolve registered, class-declared, or decorator-derived semantics."""
    key = str(backend_type).strip()
    registered = _EVENT_SEMANTICS.get(key)
    if registered is not None:
        return registered
    cached = _DERIVED_EVENT_SEMANTICS.get(key)
    if cached is not None:
        return cached

    cls = _backend_class(key)
    if cls is None:
        return EventSemantics()
    declared = parse_event_semantics(getattr(cls, "event_semantics", None))
    semantics = (
        declared
        if declared.roles or declared.fields or declared.label_tags
        else derive_event_semantics(cls)
    )
    _DERIVED_EVENT_SEMANTICS[key] = semantics
    return semantics


def _social_semantics(
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


register_event_semantics(
    "twitter_like",
    _social_semantics(
        roots={"post", "quote_repost"},
        replies={"reply"},
        reactions={"like", "repost"},
        follows={"follow", "unfollow", "mute_user", "unmute_user"},
        negative={"dislike_post", "report_post"},
        reads={"get_own_timeline", "get_trending", "timeline_retrieval"},
        content_ids=("post_id", "tweet_id"),
        response_ids=("post_id",),
        parent_ids=("reply_to_id", "target_id"),
    ),
)
register_event_semantics(
    "reddit_like",
    _social_semantics(
        roots={"post"},
        replies={"comment"},
        reactions={"upvote", "downvote"},
        follows={"mute_user", "unmute_user"},
        negative={"downvote", "dislike_post", "report_post"},
        reads={"get_home_feed", "get_post_comments", "get_trending", "timeline_retrieval"},
        content_ids=("post_id",),
        response_ids=("comment_id",),
        parent_ids=("parent_id", "post_id"),
        reaction_target_id_fields=("target_id",),
        reaction_target_type_fields=("target_type",),
    ),
)
register_event_semantics(
    "mastodon",
    _social_semantics(
        roots={"post", "post_status"},
        replies={"reply"},
        reactions={"like_toot", "boost_toot"},
        follows={"follow", "unfollow", "block_user", "mute_account"},
        negative=set(),
        reads={"get_public_timeline", "get_own_timeline", "get_user_timeline"},
        content_ids=("toot_id",),
        response_ids=("toot_id",),
        parent_ids=("reply_to.toot_id", "in_reply_to_id"),
    ),
)
