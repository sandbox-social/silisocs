"""Normalized, backend-neutral inputs for Python analysis panels."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast, overload

from silisocs.evaluations.vocabulary import EventSemantics

_UNSET = object()


@dataclass(frozen=True)
class Event:
    """One normalized committed backend action."""

    label: str
    actor: str
    episode: int
    data: Mapping[str, Any]
    backend_type: str
    gm_name: str
    tags: tuple[str, ...]
    _semantics: EventSemantics = field(repr=False, compare=False)

    def field(self, name: str) -> Any:
        """Resolve a semantic field from this event's backend payload."""
        return self._semantics.value(self.data, name)

    def labels_for(self, name: str) -> frozenset[str]:
        """Return action labels assigned to a semantic tag."""
        return self._semantics.labels(name)


def _key_value(event: Event, key: str | Callable[[Event], Hashable]) -> Hashable:
    if callable(key):
        return key(event)
    value = getattr(event, key) if hasattr(event, key) else event.field(key)
    if not isinstance(value, Hashable):
        raise TypeError(f"Event grouping key {key!r} produced an unhashable value")
    return value


class EventFrame:
    """Small immutable collection with the common event analysis operations."""

    def __init__(self, events: Iterable[Event] = ()) -> None:
        self._events = tuple(events)

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    @overload
    def __getitem__(self, item: int) -> Event: ...

    @overload
    def __getitem__(self, item: slice) -> EventFrame: ...

    def __getitem__(self, item: int | slice) -> Event | EventFrame:
        if isinstance(item, slice):
            return EventFrame(self._events[item])
        return self._events[item]

    def where(
        self,
        *,
        label: str | None | object = _UNSET,
        tag: str | None | object = _UNSET,
        actor: str | None | object = _UNSET,
        episode: int | None | object = _UNSET,
        pred: Callable[[Event], bool] | None | object = _UNSET,
    ) -> EventFrame:
        """Return matching events; omitted or explicit ``None`` means no filter."""

        def matches(event: Event) -> bool:
            if label not in (_UNSET, None) and event.label != str(label):
                return False
            if tag not in (_UNSET, None) and str(tag) not in event.tags:
                return False
            if actor not in (_UNSET, None) and event.actor != str(actor):
                return False
            if episode not in (_UNSET, None) and event.episode != cast(int, episode):
                return False
            if pred in (_UNSET, None):
                return True
            return bool(cast(Callable[[Event], bool], pred)(event))

        return EventFrame(event for event in self if matches(event))

    def group_by(self, key: str | Callable[[Event], Hashable]) -> dict[Hashable, EventFrame]:
        """Group events by an attribute, semantic field, or callable."""
        grouped: dict[Hashable, list[Event]] = {}
        for event in self:
            grouped.setdefault(_key_value(event, key), []).append(event)
        return {value: EventFrame(events) for value, events in grouped.items()}

    def by_episode(self) -> dict[int, EventFrame]:
        """Group events by episode in ascending order."""
        grouped: dict[int, list[Event]] = {}
        for event in self:
            grouped.setdefault(event.episode, []).append(event)
        return {episode: EventFrame(grouped[episode]) for episode in sorted(grouped)}

    def count_by(self, key: str | Callable[[Event], Hashable]) -> Counter[Hashable]:
        """Count events by an attribute, semantic field, or callable."""
        return Counter(_key_value(event, key) for event in self)

    def values(self, field_name: str) -> list[Any]:
        """Resolve one semantic field for every event, preserving frame order."""
        return [event.field(field_name) for event in self]

    def to_pandas(self) -> Any:
        """Return a pandas DataFrame, importing pandas only when requested."""
        import pandas as pd  # noqa: PLC0415 - deliberately optional and lazy

        return pd.DataFrame(
            [
                {
                    "label": event.label,
                    "actor": event.actor,
                    "episode": event.episode,
                    "data": dict(event.data),
                    "backend_type": event.backend_type,
                    "gm_name": event.gm_name,
                    "tags": event.tags,
                }
                for event in self
            ]
        )


def event_frame(artifact: Any) -> EventFrame:
    """Return the artifact's cached normalized committed-action frame."""
    from silisocs.analysis.panels._shared import (
        _artifact_cache,
        backend_type_for_event,
        episode_of,
        event_semantics_for_event,
    )

    cache = _artifact_cache(artifact)
    cache_key = ("event_frame",)
    if cache_key in cache:
        return cache[cache_key]

    events: list[Event] = []
    for row in getattr(artifact, "actions", ()) or ():
        semantics = event_semantics_for_event(row, artifact)
        label = str(row.get("label") or "unknown")
        raw_data = row.get("data")
        data = dict(raw_data) if isinstance(raw_data, Mapping) else {}
        events.append(
            Event(
                label=label,
                actor=str(row.get("source_user") or "Unknown"),
                episode=episode_of(row),
                data=MappingProxyType(data),
                backend_type=backend_type_for_event(row, artifact),
                gm_name=str(row.get("gm_name") or ""),
                tags=semantics.tags_of(label),
                _semantics=semantics,
            )
        )
    frame = EventFrame(events)
    cache[cache_key] = frame
    return frame


@dataclass(frozen=True)
class ProbeEvent:
    """One normalized response from the canonical probe event row."""

    probe: str
    agent: str
    episode: int
    anchor: str
    value: Any
    raw: Mapping[str, Any]


def probe_frame(artifact: Any) -> tuple[ProbeEvent, ...]:
    """Return cached normalized probe responses in artifact order."""
    from silisocs.analysis.panels._shared import _artifact_cache

    cache = _artifact_cache(artifact)
    cache_key = ("probe_frame",)
    if cache_key in cache:
        return cache[cache_key]

    events: list[ProbeEvent] = []
    for row in getattr(artifact, "probes", ()) or ():
        payload = row["data"]
        if not isinstance(payload, Mapping):
            raise TypeError("Probe event data must be a mapping.")
        events.append(
            ProbeEvent(
                probe=str(row["label"]),
                agent=str(row["source_user"]),
                episode=int(row["episode"]),
                anchor=str(row["anchor"]),
                value=payload["probe_return"],
                raw=MappingProxyType(dict(row)),
            )
        )
    result = tuple(events)
    cache[cache_key] = result
    return result
