"""Backend action vocabularies shared by analysis and visual surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class ActionVocabulary:
    """Semantic groups for aggregate analysis of logged action labels."""

    creates_content: frozenset[str] = frozenset()
    endorses: frozenset[str] = frozenset()
    negative: frozenset[str] = frozenset()
    social_graph: frozenset[str] = frozenset()
    reads: frozenset[str] = frozenset()

    @property
    def interactions(self) -> frozenset[str]:
        """Return actions useful in interaction analysis."""
        return self.creates_content | self.endorses | self.negative


_VOCABULARIES: dict[str, ActionVocabulary] = {}


@dataclass(frozen=True)
class EventSemantics:
    """Namespaced semantic roles and payload paths for optional visual capabilities.

    Role and field names are deliberately open strings. A panel defines the
    names it understands (for example ``content.root``); a backend registers
    labels and dotted payload paths against those names. This keeps specialized
    visual knowledge out of the artifact and panel contracts.
    """

    roles: Mapping[str, frozenset[str]] = field(default_factory=dict)
    fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

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

    def labels(self, role: str) -> frozenset[str]:
        """Return labels registered for a semantic role."""
        return self.roles.get(role, frozenset())

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


def register_action_vocabulary(backend_type: str, vocabulary: ActionVocabulary) -> None:
    """Register or replace the vocabulary for a backend type."""
    key = str(backend_type).strip()
    if not key:
        raise ValueError("backend_type must be non-empty")
    if not isinstance(vocabulary, ActionVocabulary):
        raise TypeError("vocabulary must be an ActionVocabulary")
    _VOCABULARIES[key] = vocabulary


def vocabulary_for(backend_type: str) -> ActionVocabulary:
    """Return a registered vocabulary, or an empty vocabulary when unknown."""
    return _VOCABULARIES.get(str(backend_type).strip(), ActionVocabulary())


def register_event_semantics(backend_type: str, semantics: EventSemantics) -> None:
    """Register optional visual semantics for a backend type."""
    key = str(backend_type).strip()
    if not key:
        raise ValueError("backend_type must be non-empty")
    if not isinstance(semantics, EventSemantics):
        raise TypeError("semantics must be EventSemantics")
    _EVENT_SEMANTICS[key] = semantics


def event_semantics_for(backend_type: str) -> EventSemantics:
    """Return registered semantics, or an empty capability for unknown backends."""
    return _EVENT_SEMANTICS.get(str(backend_type).strip(), EventSemantics())


def infer_event_semantics(label: str) -> EventSemantics:
    """Merge registered capabilities that recognize a legacy untyped event label."""
    matching = [
        semantics
        for semantics in _EVENT_SEMANTICS.values()
        if any(label in labels for labels in semantics.roles.values())
    ]
    if not matching:
        return EventSemantics()
    roles: dict[str, frozenset[str]] = {}
    fields: dict[str, tuple[str, ...]] = {}
    for semantics in matching:
        for role, labels in semantics.roles.items():
            roles[role] = roles.get(role, frozenset()) | labels
        for name, paths in semantics.fields.items():
            fields[name] = tuple(dict.fromkeys((*fields.get(name, ()), *paths)))
    return EventSemantics(roles=roles, fields=fields)


# Group members must be the exact `label=` strings the backends pass to
# _log_action_event — grep `label="` in the backend app when adding one.
register_action_vocabulary(
    "twitter_like",
    ActionVocabulary(
        creates_content=frozenset({"post", "reply", "quote_repost"}),
        endorses=frozenset({"like", "repost"}),
        negative=frozenset({"dislike_post", "report_post"}),
        social_graph=frozenset({"follow", "unfollow", "mute_user", "unmute_user"}),
        reads=frozenset({"get_own_timeline", "get_trending", "timeline_retrieval"}),
    ),
)
register_action_vocabulary(
    "mastodon",
    ActionVocabulary(
        creates_content=frozenset({"post", "post_status", "reply"}),
        endorses=frozenset({"like_toot", "boost_toot"}),
        social_graph=frozenset({"follow", "unfollow", "block_user", "mute_account"}),
        reads=frozenset({"get_public_timeline", "get_own_timeline", "get_user_timeline"}),
    ),
)
register_action_vocabulary(
    "reddit_like",
    ActionVocabulary(
        creates_content=frozenset({"post", "comment"}),
        endorses=frozenset({"upvote"}),
        negative=frozenset({"downvote", "dislike_post", "report_post"}),
        social_graph=frozenset({"mute_user", "unmute_user"}),
        reads=frozenset(
            {"get_home_feed", "get_post_comments", "get_trending", "timeline_retrieval"}
        ),
    ),
)


def _social_semantics(
    *,
    roots: set[str],
    replies: set[str],
    reactions: set[str],
    follows: set[str],
    content_ids: tuple[str, ...],
    response_ids: tuple[str, ...],
    parent_ids: tuple[str, ...],
) -> EventSemantics:
    return EventSemantics(
        roles={
            "content.root": frozenset(roots),
            "content.reply": frozenset(replies),
            "interaction.reaction": frozenset(reactions),
            "network.follow": frozenset(follows),
        },
        fields={
            "content.id": content_ids,
            "content.response_id": response_ids,
            "content.parent_id": parent_ids,
            "content.text": ("post_text", "content", "text", "title"),
            "network.target_actor": ("target_user", "target_username"),
        },
    )


register_event_semantics(
    "twitter_like",
    _social_semantics(
        roots={"post", "quote_repost"},
        replies={"reply"},
        reactions={"like", "repost"},
        follows={"follow"},
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
        follows=set(),
        content_ids=("post_id",),
        response_ids=("comment_id",),
        parent_ids=("parent_id", "post_id"),
    ),
)
register_event_semantics(
    "mastodon",
    _social_semantics(
        roots={"post", "post_status"},
        replies={"reply"},
        reactions={"like_toot", "boost_toot"},
        follows={"follow"},
        content_ids=("toot_id",),
        response_ids=("toot_id",),
        parent_ids=("reply_to.toot_id", "in_reply_to_id"),
    ),
)
