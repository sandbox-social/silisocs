"""Backend-neutral state, capabilities, and queries for artifact exploration."""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.metadata
import json
import threading
import warnings
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from silisocs.analysis.panel import list_panels
from silisocs.analysis.panels._shared import (
    backend_types,
    event_semantics_for_event,
    run_semantic_roles,
)
from silisocs.analysis.views import declared_skip_reason
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact

ScopeKind = Literal["run", "study"]

_STREAM_READERS = {
    "action": "iter_actions",
    "exposure": "iter_exposures",
    "probe": "iter_probes",
    "harness": "iter_harness_events",
}
_STREAM_REQUIREMENTS = {
    "action_events": "action",
    "exposure_events": "exposure",
    "probe_events": "probe",
    "harness_events": "harness",
}


def _values(params: Mapping[str, Any], key: str) -> tuple[str, ...]:
    getter = getattr(params, "getlist", None)
    raw = getter(key) if callable(getter) else params.get(key, ())
    values = raw if isinstance(raw, (list, tuple)) else (raw,)
    return tuple(str(value).strip() for value in values if str(value or "").strip())


def _scalar(params: Mapping[str, Any], key: str) -> Any:
    raw = params.get(key)
    return raw[-1] if isinstance(raw, (list, tuple)) and raw else raw


def _integer(params: Mapping[str, Any], key: str) -> int | None:
    raw = _scalar(params, key)
    if raw in (None, ""):
        return None
    try:
        return int(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc


@dataclass(frozen=True)
class ExplorationState:
    """One serializable navigation state shared by every exploration surface."""

    scope_kind: ScopeKind
    scope_ids: tuple[str, ...]
    lens: str = "pulse"
    scene: str | None = None
    time_start: int | None = None
    time_end: int | None = None
    entity_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    cohort_ids: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    actors: tuple[str, ...] = ()
    backend_types: tuple[str, ...] = ()
    comparison_dimension: str | None = None
    comparison_baseline: str | None = None

    @classmethod
    def from_query(
        cls,
        scope_kind: ScopeKind,
        scope_ids: Sequence[str],
        params: Mapping[str, Any],
    ) -> ExplorationState:
        """Parse state from URL query parameters without knowing any backend."""
        lens = str(_scalar(params, "lens") or "pulse").strip()
        return cls(
            scope_kind=scope_kind,
            scope_ids=tuple(str(value) for value in scope_ids),
            lens=lens,
            scene=str(_scalar(params, "scene") or "").strip() or None,
            time_start=_integer(params, "start"),
            time_end=_integer(params, "end"),
            entity_ids=_values(params, "entity"),
            event_ids=_values(params, "event"),
            cohort_ids=_values(params, "cohort"),
            labels=_values(params, "label"),
            tags=_values(params, "tag"),
            actors=_values(params, "actor"),
            backend_types=_values(params, "backend_type"),
            comparison_dimension=str(_scalar(params, "compare") or "").strip() or None,
            comparison_baseline=str(_scalar(params, "baseline") or "").strip() or None,
        )

    def query_items(self) -> list[tuple[str, str]]:
        """Return canonical URL query items, omitting default and empty values."""
        items: list[tuple[str, str]] = []
        if self.lens != "pulse":
            items.append(("lens", self.lens))
        scalar_values = (
            ("scene", self.scene),
            ("start", self.time_start),
            ("end", self.time_end),
            ("compare", self.comparison_dimension),
            ("baseline", self.comparison_baseline),
        )
        items.extend((key, str(value)) for key, value in scalar_values if value is not None)
        list_values = (
            ("entity", self.entity_ids),
            ("event", self.event_ids),
            ("cohort", self.cohort_ids),
            ("label", self.labels),
            ("tag", self.tags),
            ("actor", self.actors),
            ("backend_type", self.backend_types),
        )
        for key, values in list_values:
            items.extend((key, value) for value in values)
        return items

    def to_dict(self) -> dict[str, Any]:
        """Return the nested public state contract consumed by browser clients."""
        return {
            "scope": {"kind": self.scope_kind, "ids": list(self.scope_ids)},
            "time": {"start": self.time_start, "end": self.time_end},
            "selection": {
                "entity_ids": list(self.entity_ids),
                "event_ids": list(self.event_ids),
                "cohort_ids": list(self.cohort_ids),
            },
            "filters": {
                "labels": list(self.labels),
                "tags": list(self.tags),
                "actors": list(self.actors),
                "backend_types": list(self.backend_types),
            },
            "comparison": {
                "dimension": self.comparison_dimension,
                "baseline": self.comparison_baseline,
            },
            "lens": {"name": self.lens, "scene": self.scene},
        }


@dataclass(frozen=True)
class SceneSpec:
    """A registered exploration scene selected only through capabilities."""

    id: str
    title: str
    scope: ScopeKind
    lens: str
    renderer: str
    requires_any: frozenset[str] = frozenset()
    requires_all: frozenset[str] = frozenset()
    selection_kinds: tuple[str, ...] = ()
    order: int = 100


class ExplorationScene:
    """Class-path extension contract for project-defined exploration scenes."""

    id = ""
    title = ""
    scope: ScopeKind = "run"
    lens = ""
    renderer = ""
    requires_any: frozenset[str] = frozenset()
    requires_all: frozenset[str] = frozenset()
    selection_kinds: tuple[str, ...] = ()
    order = 100

    @classmethod
    def spec(cls) -> SceneSpec:
        """Project the class declaration as an immutable runtime descriptor."""
        return SceneSpec(
            id=str(cls.id),
            title=str(cls.title or cls.id),
            scope=cls.scope,
            lens=str(cls.lens or cls.id),
            renderer=str(cls.renderer),
            requires_any=frozenset(cls.requires_any),
            requires_all=frozenset(cls.requires_all),
            selection_kinds=tuple(cls.selection_kinds),
            order=int(cls.order),
        )


Scene = SceneSpec

_SCENES: dict[str, SceneSpec] = {}
_SCENE_ENTRY_POINTS_LOADED: list[bool] = []


def register_scene(scene: SceneSpec | type[ExplorationScene]) -> SceneSpec:
    """Register a scene; projects can call this or publish a ``silisocs.scenes`` entry point."""
    if isinstance(scene, type) and issubclass(scene, ExplorationScene):
        scene = scene.spec()
    if not isinstance(scene, SceneSpec):
        raise TypeError("register_scene expects a SceneSpec or ExplorationScene subclass")
    if not scene.id.strip() or not scene.renderer.strip():
        raise ValueError("Scene id and renderer must be non-empty")
    _SCENES[scene.id] = scene
    return scene


def _load_scene_entry_points() -> None:
    if _SCENE_ENTRY_POINTS_LOADED:
        return
    _SCENE_ENTRY_POINTS_LOADED.append(True)
    for entry_point in importlib.metadata.entry_points(group="silisocs.scenes"):
        try:
            register_scene(entry_point.load())
        except Exception as exc:
            warnings.warn(
                f"Skipping exploration scene {entry_point.name!r}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )


def list_scenes(scope: ScopeKind, features: Iterable[str]) -> list[SceneSpec]:
    """Return registered scenes supported by the artifact feature set."""
    _load_scene_entry_points()
    available = frozenset(features)
    return sorted(
        (
            scene
            for scene in _SCENES.values()
            if scene.scope == scope
            and scene.requires_all <= available
            and (not scene.requires_any or bool(scene.requires_any & available))
        ),
        key=lambda scene: (scene.order, scene.title, scene.id),
    )


for _scene in (
    Scene(
        "pulse",
        "Pulse",
        "run",
        "pulse",
        "series",
        frozenset({"metrics", "event_stream.action", "event_stream.probe"}),
        selection_kinds=("interval",),
        order=10,
    ),
    Scene(
        "entities",
        "Entities",
        "run",
        "entities",
        "entities",
        frozenset({"entities"}),
        selection_kinds=("entity",),
        order=20,
    ),
    Scene(
        "events",
        "Events",
        "run",
        "events",
        "events",
        frozenset({"events"}),
        selection_kinds=("event", "entity", "interval"),
        order=30,
    ),
    Scene(
        "space",
        "Space",
        "run",
        "space",
        "viewer",
        frozenset({"viewer"}),
        selection_kinds=("entity",),
        order=40,
    ),
    Scene(
        "compare",
        "Compare",
        "study",
        "compare",
        "comparison",
        frozenset({"study.comparison"}),
        selection_kinds=("cohort", "interval"),
        order=10,
    ),
):
    register_scene(_scene)


def _scene_json(scene: SceneSpec) -> dict[str, Any]:
    return {
        **asdict(scene),
        "requires_any": sorted(scene.requires_any),
        "requires_all": sorted(scene.requires_all),
    }


def _panel_json(panel: Any, *, available: bool) -> dict[str, Any]:
    return {
        "id": panel.name,
        "title": panel.title,
        "available": available,
        "requires": sorted(panel.requires),
        "semantics": sorted(panel.semantics),
    }


def _recorded_streams(artifact: RunArtifact) -> tuple[str, ...]:
    artifacts = artifact.manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return ()
    return tuple(
        stream
        for requirement, stream in _STREAM_REQUIREMENTS.items()
        if isinstance(artifacts.get(requirement), list) and artifacts[requirement]
    )


def _viewer_backends(artifact: RunArtifact) -> tuple[str, ...]:
    found: list[str] = []
    for game_master in artifact.game_masters:
        backend_type = str(game_master.get("backend_type") or "").strip()
        database = game_master.get("database")
        visualizer = game_master.get("visualizer")
        path = Path(str(database)) if isinstance(database, str) and database else None
        if path is not None and not path.is_absolute():
            path = artifact.run_dir / path
        if backend_type and isinstance(visualizer, Mapping) and path is not None and path.is_file():
            found.append(backend_type)
    return tuple(dict.fromkeys(found))


def run_capability_document(artifact: RunArtifact, artifact_id: str) -> dict[str, Any]:
    """Build the lightweight capability/index document for one run."""
    streams = _recorded_streams(artifact)
    roles = run_semantic_roles(artifact)
    viewers = _viewer_backends(artifact)
    features = {"events"} if streams else set()
    features.update(f"event_stream.{stream}" for stream in streams)
    if artifact.num_agents or {"action", "probe"} & set(streams):
        features.add("entities")
    if artifact.metrics is not None:
        features.add("metrics")
    if viewers:
        features.add("viewer")
    if has_relationships(roles):
        features.add("relationships")

    panels = []
    for panel in list_panels():
        if panel.scope != "run":
            continue
        # Declaration-level availability from the SAME predicate `build_view`
        # renders with (its lazy tier): no hand-rolled copy to drift. The
        # capability request must not parse logs, so a panel's data-based
        # `applicable()` override resolves at render time — such a panel may
        # still render as "nothing in this run to show".
        panels.append(_panel_json(panel, available=not declared_skip_reason(panel, artifact)))
    if any(panel["available"] for panel in panels):
        features.add("panels")

    scenes = list_scenes("run", features)
    return {
        "artifact": {
            "id": artifact_id,
            "kind": "run",
            "scenario": artifact.scenario,
            "status": artifact.status,
            "episodes": artifact.num_steps,
            "entities": artifact.num_agents,
            "fingerprint": _artifact_fingerprint(artifact),
        },
        "features": sorted(features),
        "streams": list(streams),
        "backend_types": backend_types(artifact),
        "semantic_roles": sorted(roles),
        "viewer_backends": list(viewers),
        "scenes": [_scene_json(scene) for scene in scenes],
        "panels": panels,
        "evidence": {
            "health": artifact.health,
            "provenance": artifact.provenance,
            "has_probes": "probe" in streams,
            "has_story": bool(streams),
            "has_relationships": has_relationships(roles),
        },
    }


def study_capability_document(artifact: StudyArtifact, artifact_id: str) -> dict[str, Any]:
    """Build the capability document for a study without opening its run logs."""
    definition = artifact.definition or {}
    study = definition.get("study")
    study = study if isinstance(study, Mapping) else {}
    hypotheses = definition.get("hypotheses")
    hypotheses = hypotheses if isinstance(hypotheses, Mapping) else {}
    progress = artifact.progress
    status_counts = Counter(str(row.get("status") or "unknown") for row in progress)
    features = {"study.comparison", "panels"}
    scenes = list_scenes("study", features)
    panels = [
        _panel_json(panel, available=True) for panel in list_panels() if panel.scope == "study"
    ]
    fingerprint_paths = [
        artifact.study_dir / "study.yaml",
        artifact.study_dir / "generated" / "organized" / "study_summary.yaml",
    ]
    return {
        "artifact": {
            "id": artifact_id,
            "kind": "study",
            "name": study.get("name") or artifact_id,
            "question": study.get("question") or "",
            "runs": len(progress),
            "hypotheses": len(hypotheses),
            "fingerprint": _paths_fingerprint(fingerprint_paths),
        },
        "features": sorted(features),
        "scenes": [_scene_json(scene) for scene in scenes],
        "panels": panels,
        "evidence": {
            "progress": dict(status_counts),
            "has_summary": artifact.summary is not None,
            "has_plan": artifact.plan is not None,
        },
    }


def _paths_fingerprint(paths: Iterable[Path]) -> str:
    parts = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append((str(path), stat.st_size, stat.st_mtime_ns))
    payload = json.dumps(parts, separators=(",", ":")).encode()
    return hashlib.blake2b(payload, digest_size=12).hexdigest()


def _artifact_fingerprint(artifact: RunArtifact) -> str:
    parts: list[tuple[str, int, int]] = []
    artifacts = artifact.manifest.get("artifacts")
    for entries in artifacts.values() if isinstance(artifacts, Mapping) else ():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, str):
                continue
            path = artifact.run_dir / entry
            try:
                stat = path.stat()
            except OSError:
                continue
            parts.append((entry, stat.st_size, stat.st_mtime_ns))
    payload = json.dumps(sorted(parts), separators=(",", ":")).encode()
    return hashlib.blake2b(payload, digest_size=12).hexdigest()


# One shared, bounded memo for the heavy artifact queries. The key includes the
# artifact fingerprint (event-file sizes + mtimes) AND the run's semantic-role set,
# so a completed run answers a repeated cross-filter from cache while a live run
# growing on disk — or a change to the manifest's label→role mapping — invalidates
# itself. ``ExplorationQuery`` is a frozen dataclass, hence hashable as part of the
# key. Cached results are SHARED across callers and MUST be treated as read-only.
_QUERY_CACHE: dict[tuple[Any, ...], Any] = {}
_QUERY_CACHE_ORDER: list[tuple[Any, ...]] = []
_QUERY_CACHE_LOCK = threading.Lock()
_QUERY_CACHE_MAX = 256


def _cached_query(artifact: RunArtifact, kind: str, query: Any, compute: Any) -> Any:
    key = (
        artifact.run_dir.as_posix(),
        _artifact_fingerprint(artifact),
        run_semantic_roles(artifact),
        kind,
        query,
    )
    with _QUERY_CACHE_LOCK:
        if key in _QUERY_CACHE:
            return _QUERY_CACHE[key]
    result = compute()
    with _QUERY_CACHE_LOCK:
        if key not in _QUERY_CACHE:
            _QUERY_CACHE[key] = result
            _QUERY_CACHE_ORDER.append(key)
            while len(_QUERY_CACHE_ORDER) > _QUERY_CACHE_MAX:
                _QUERY_CACHE.pop(_QUERY_CACHE_ORDER.pop(0), None)
    return result


@dataclass(frozen=True)
class ExplorationQuery:
    """Filters and paging shared by event, entity, and series queries."""

    start: int | None = None
    end: int | None = None
    labels: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    actors: tuple[str, ...] = ()
    backend_types: tuple[str, ...] = ()
    streams: tuple[str, ...] = ()
    cursor: str | None = None
    limit: int = 100

    @classmethod
    def from_mapping(cls, params: Mapping[str, Any]) -> ExplorationQuery:
        """Parse an HTTP-style mapping and bound its page size."""
        streams = _values(params, "stream")
        unknown = set(streams) - set(_STREAM_READERS)
        if unknown:
            raise ValueError(f"Unknown event streams: {', '.join(sorted(unknown))}")
        try:
            limit = int(str(_scalar(params, "limit") or 100))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer") from exc
        return cls(
            start=_integer(params, "start"),
            end=_integer(params, "end"),
            labels=_values(params, "label"),
            tags=_values(params, "tag"),
            actors=_values(params, "actor"),
            backend_types=_values(params, "backend_type"),
            streams=streams,
            cursor=str(_scalar(params, "cursor") or "").strip() or None,
            limit=max(1, min(limit, 500)),
        )


def _cursor_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        offset = int(payload["offset"])
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("Invalid exploration cursor") from exc
    if offset < 0:
        raise ValueError("Invalid exploration cursor")
    return offset


def _cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _episode(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("episode", 0))
    except (TypeError, ValueError):
        return 0


def _event_tags(stream: str, row: Mapping[str, Any], artifact: RunArtifact) -> tuple[str, ...]:
    if stream != "action":
        raw = row.get("tags")
        return tuple(str(value) for value in raw) if isinstance(raw, (list, tuple)) else ()
    label = str(row.get("label") or "")
    return event_semantics_for_event(dict(row), artifact).tags_of(label)


def _matches(
    stream: str,
    row: Mapping[str, Any],
    artifact: RunArtifact,
    query: ExplorationQuery,
) -> bool:
    episode = _episode(row)
    label = str(row.get("label") or "")
    actor = str(row.get("source_user") or "")
    backend_type = str(row.get("backend_type") or "")
    tags = _event_tags(stream, row, artifact)
    return not (
        (query.start is not None and episode < query.start)
        or (query.end is not None and episode > query.end)
        or (query.labels and label not in query.labels)
        or (query.actors and actor not in query.actors)
        or (query.backend_types and backend_type not in query.backend_types)
        or (query.tags and not set(query.tags) <= set(tags))
    )


def _iter_events(
    artifact: RunArtifact, query: ExplorationQuery
) -> Iterable[tuple[str, int, Mapping[str, Any]]]:
    streams = query.streams or _recorded_streams(artifact)
    for stream in streams:
        reader = getattr(artifact, _STREAM_READERS[stream])
        for index, row in enumerate(reader()):
            if _matches(stream, row, artifact, query):
                yield stream, index, row


def query_events(artifact: RunArtifact, query: ExplorationQuery) -> dict[str, Any]:
    """Return a stable cursor page without materializing every event stream."""
    return _cached_query(artifact, "events", query, lambda: _query_events(artifact, query))


def _query_events(artifact: RunArtifact, query: ExplorationQuery) -> dict[str, Any]:
    offset = _cursor_offset(query.cursor)
    selected: list[dict[str, Any]] = []
    matched = 0
    has_more = False
    for stream, index, row in _iter_events(artifact, query):
        if matched < offset:
            matched += 1
            continue
        if len(selected) >= query.limit:
            has_more = True
            break
        tags = _event_tags(stream, row, artifact)
        selected.append(
            {
                "id": f"{stream}:{index}",
                "stream": stream,
                "episode": _episode(row),
                "label": str(row.get("label") or stream),
                "actor": str(row.get("source_user") or ""),
                "backend_type": str(row.get("backend_type") or ""),
                "gm_name": str(row.get("gm_name") or ""),
                "tags": list(tags),
                "data": row.get("data") if isinstance(row.get("data"), Mapping) else {},
            }
        )
        matched += 1
    next_offset = offset + len(selected)
    return {
        "items": selected,
        "next_cursor": _cursor(next_offset) if has_more else None,
        "has_more": has_more,
    }


def query_entities(artifact: RunArtifact, query: ExplorationQuery) -> dict[str, Any]:
    """Aggregate actors from the selected event streams into a cursor page."""
    return _cached_query(artifact, "entities", query, lambda: _query_entities(artifact, query))


def _query_entities(artifact: RunArtifact, query: ExplorationQuery) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    for stream, _, row in _iter_events(artifact, query):
        actor = str(row.get("source_user") or "")
        if not actor:
            continue
        counts[actor] += 1
        labels[actor][str(row.get("label") or stream)] += 1
    entities = [
        {
            "id": actor,
            "name": actor,
            "event_count": count,
            "top_labels": [label for label, _ in labels[actor].most_common(3)],
        }
        for actor, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    offset = _cursor_offset(query.cursor)
    page = entities[offset : offset + query.limit]
    has_more = offset + len(page) < len(entities)
    return {
        "items": page,
        "next_cursor": _cursor(offset + len(page)) if has_more else None,
        "has_more": has_more,
        "total": len(entities),
    }


def query_series(artifact: RunArtifact, query: ExplorationQuery) -> dict[str, Any]:
    """Count matching events by episode and open label for the Pulse scene."""
    return _cached_query(artifact, "series", query, lambda: _query_series(artifact, query))


def _query_series(artifact: RunArtifact, query: ExplorationQuery) -> dict[str, Any]:
    counts: dict[str, Counter[int]] = defaultdict(Counter)
    episodes: set[int] = set()
    for stream, _, row in _iter_events(artifact, query):
        episode = _episode(row)
        label = str(row.get("label") or stream)
        counts[label][episode] += 1
        episodes.add(episode)
    domain = sorted(episodes)
    return {
        "episodes": domain,
        "series": [
            {
                "id": label,
                "label": label,
                "values": [episode_counts[episode] for episode in domain],
            }
            for label, episode_counts in sorted(counts.items())
        ],
    }


# Roles a backend declares outright when an event names its target actor. Using
# only these (never reply/reaction thread reconstruction) keeps the relationship
# query backend-neutral: any backend mapping its labels to these open roles gets a
# graph, with no content-owner bookkeeping.
_DIRECT_EDGE_ROLES = frozenset({"network.follow", "interaction.directed"})


def _direct_edges(
    artifact: RunArtifact, query: ExplorationQuery
) -> tuple[Counter[str], Counter[tuple[str, str, str]]]:
    nodes: Counter[str] = Counter()
    edges: Counter[tuple[str, str, str]] = Counter()
    for stream, _, row in _iter_events(artifact, query):
        if stream != "action":
            continue
        source = str(row.get("source_user") or "")
        if not source:
            continue
        nodes[source] += 1
        semantics = event_semantics_for_event(dict(row), artifact)
        tags = set(semantics.tags_of(str(row.get("label") or "")))
        if not (_DIRECT_EDGE_ROLES & tags):
            continue
        kind = "follow" if "network.follow" in tags else "interaction"
        raw_data = row.get("data")
        data = raw_data if isinstance(raw_data, Mapping) else {}
        target = semantics.value(data, "network.target_actor")
        if target is None:
            continue
        nodes[str(target)] += 0  # ensure the target is a node even if it never acts
        edges[(source, str(target), kind)] += 1
    return nodes, edges


def has_relationships(roles: Iterable[str]) -> bool:
    """Whether a run's declared semantic roles can form a directed actor graph."""
    return bool(_DIRECT_EDGE_ROLES & frozenset(roles))


def query_relationships(artifact: RunArtifact, query: ExplorationQuery) -> dict[str, Any]:
    """Return the directed actor→actor graph a backend declares through open roles."""
    return _cached_query(
        artifact, "relationships", query, lambda: _query_relationships(artifact, query)
    )


def _query_relationships(artifact: RunArtifact, query: ExplorationQuery) -> dict[str, Any]:
    nodes, edges = _direct_edges(artifact, query)
    return {
        "directed": True,
        "nodes": [
            {"id": name, "event_count": count}
            for name, count in sorted(nodes.items(), key=lambda item: (-item[1], item[0]))
        ],
        "edges": [
            {"source": source, "target": target, "kind": kind, "weight": weight}
            for (source, target, kind), weight in sorted(edges.items())
        ],
    }


def _entity_neighbors(
    artifact: RunArtifact, query: ExplorationQuery, entity: str
) -> dict[str, Any]:
    _, edges = _direct_edges(artifact, query)
    out = Counter[str]()
    incoming = Counter[str]()
    for (source, target, _), weight in edges.items():
        if source == entity:
            out[target] += weight
        if target == entity:
            incoming[source] += weight
    top = lambda counter: [name for name, _ in counter.most_common(5)]  # noqa: E731
    return {"out": top(out), "in": top(incoming)}


def query_evidence(
    artifact: RunArtifact,
    query: ExplorationQuery,
    *,
    entity: str | None = None,
    episode: int | None = None,
) -> dict[str, Any]:
    """Return deterministic, deep-linkable evidence for the current selection.

    Every item names its own ``refs`` — exploration query params a client turns
    into a shareable URL — so an observation always points back at the episode,
    actor, or filter that produced it. No item is ever LLM-generated.
    """
    items: list[dict[str, Any]] = []
    for name, value in (artifact.health or {}).items():
        if value:
            items.append(
                {
                    "kind": "health",
                    "title": name.replace("_", " "),
                    "detail": f"{value} recorded",
                    "refs": [],
                }
            )
    if entity:
        activity = sum(1 for _, _, row in _iter_events(artifact, query) if _actor(row) == entity)
        items.append(
            {
                "kind": "entity",
                "title": entity,
                "detail": f"{activity} attributed events in view",
                "refs": [{"label": "Focus this actor", "params": {"actor": [entity]}}],
            }
        )
        responses = [
            {"probe": str(row.get("label") or ""), "value": _probe_value(row)}
            for row in artifact.iter_probes()
            if _actor(row) == entity and _within_window(row, query)
        ]
        if responses:
            latest = {row["probe"]: row["value"] for row in responses}  # last write per probe
            items.append(
                {
                    "kind": "probe",
                    "title": f"{len(responses)} probe responses",
                    "detail": "; ".join(f"{name}: {value}" for name, value in latest.items())[:200],
                    "refs": [],
                }
            )
        neighbors = _entity_neighbors(artifact, query, entity)
        if neighbors["out"] or neighbors["in"]:
            items.append(
                {
                    "kind": "relationship",
                    "title": "Connections",
                    "detail": _neighbor_detail(neighbors),
                    "refs": [
                        {"label": name, "params": {"actor": [name]}}
                        for name in dict.fromkeys(neighbors["out"] + neighbors["in"])
                    ],
                }
            )
    if episode is not None:
        window = ExplorationQuery(
            start=episode,
            end=episode,
            labels=query.labels,
            tags=query.tags,
            actors=query.actors,
            backend_types=query.backend_types,
            streams=query.streams,
        )
        labels = Counter[str]()
        volume = 0
        for stream, _, row in _iter_events(artifact, window):
            labels[str(row.get("label") or stream)] += 1
            volume += 1
        items.append(
            {
                "kind": "interval",
                "title": f"Episode {episode}",
                "detail": f"{volume} events · "
                + ", ".join(f"{name}: {count}" for name, count in labels.most_common(4)),
                "refs": [
                    {"label": "Isolate episode", "params": {"start": episode, "end": episode}}
                ],
            }
        )
    return {"items": items, "focus": {"entity": entity, "episode": episode}}


def _neighbor_detail(neighbors: Mapping[str, list[str]]) -> str:
    parts = []
    if neighbors["out"]:
        parts.append("→ " + ", ".join(neighbors["out"]))
    if neighbors["in"]:
        parts.append("← " + ", ".join(neighbors["in"]))
    return " · ".join(parts)


def run_story(artifact: RunArtifact) -> dict[str, Any]:
    """Build a deterministic, evidence-linked narrative of a completed run.

    Every beat is derived from recorded counts (episode volume, open labels,
    actors, declared relationships, health, probes) and carries ``refs`` that
    deep-link to the evidence behind it. Nothing here is model-generated.
    """
    return _cached_query(artifact, "story", ExplorationQuery(), lambda: _run_story(artifact))


def _episode_beat(kind: str, title: str, detail: str, episode: int) -> dict[str, Any]:
    return _beat(
        kind,
        title,
        detail,
        episode=episode,
        refs=[{"label": f"Open episode {episode}", "params": {"start": episode, "end": episode}}],
    )


def _volume_beats(per_episode: Counter[int]) -> list[dict[str, Any]]:
    peak = max(per_episode, key=lambda episode: (per_episode[episode], -episode))
    beats = [
        _episode_beat(
            "peak",
            f"Activity peaked at episode {peak}",
            f"{per_episode[peak]} events — the run's busiest step",
            peak,
        )
    ]
    ordered = sorted(per_episode)
    if len(ordered) > 1:
        jump, delta = max(
            (
                (episode, per_episode[episode] - per_episode[prev])
                for prev, episode in pairwise(ordered)
            ),
            key=lambda item: (abs(item[1]), -item[0]),
        )
        if delta:
            direction = "surged" if delta > 0 else "fell"
            beats.append(
                _episode_beat(
                    "turn",
                    f"Activity {direction} into episode {jump}",
                    f"{'+' if delta > 0 else ''}{delta} events versus the previous step",
                    jump,
                )
            )
    return beats


def _run_story(artifact: RunArtifact) -> dict[str, Any]:
    per_episode: Counter[int] = Counter()
    labels: Counter[str] = Counter()
    actors: Counter[str] = Counter()
    total = 0
    for stream, _, row in _iter_events(artifact, ExplorationQuery()):
        per_episode[_episode(row)] += 1
        labels[str(row.get("label") or stream)] += 1
        if _actor(row):
            actors[_actor(row)] += 1
        total += 1
    beats: list[dict[str, Any]] = []
    if total:
        beats.extend(_volume_beats(per_episode))
        label, label_count = labels.most_common(1)[0]
        beats.append(
            _beat(
                "behavior",
                f"“{label}” dominated behavior",
                f"{label_count} of {total} events ({round(100 * label_count / total)}%)",
                refs=[{"label": f"Filter to {label}", "params": {"label": [label]}}],
            )
        )
        if actors:
            actor, actor_count = actors.most_common(1)[0]
            beats.append(
                _beat(
                    "actor",
                    f"{actor} was the most active agent",
                    f"{actor_count} attributed events",
                    refs=[{"label": f"Focus {actor}", "params": {"actor": [actor]}}],
                )
            )
        _, edges = _direct_edges(artifact, ExplorationQuery())
        if edges:
            connected = {name for name, _, _ in edges} | {target for _, target, _ in edges}
            beats.append(
                _beat(
                    "network",
                    "A relationship structure emerged",
                    f"{sum(edges.values())} directed connections among {len(connected)} agents",
                    refs=[],
                )
            )
    issues = {name: value for name, value in (artifact.health or {}).items() if value}
    if issues:
        beats.append(
            _beat(
                "health",
                "The run reported degraded health",
                ", ".join(f"{name.replace('_', ' ')}: {value}" for name, value in issues.items()),
                refs=[],
                tone="warning",
            )
        )
    if "probe" in _recorded_streams(artifact):
        probe_labels = {str(row.get("label") or "") for row in artifact.iter_probes()}
        if probe_labels:
            beats.append(
                _beat(
                    "probes",
                    f"{len(probe_labels)} evaluation probes were measured",
                    ", ".join(sorted(probe_labels))[:200],
                    refs=[],
                )
            )
    return {
        "beats": beats,
        "totals": {"events": total, "episodes": len(per_episode), "actors": len(actors)},
    }


def _beat(
    beat_id: str,
    title: str,
    detail: str,
    *,
    episode: int | None = None,
    refs: list[dict[str, Any]] | None = None,
    tone: str = "neutral",
) -> dict[str, Any]:
    return {
        "id": beat_id,
        "title": title,
        "detail": detail,
        "episode": episode,
        "tone": tone,
        "refs": refs or [],
    }


def compare_runs(
    artifacts: Sequence[tuple[str, RunArtifact]], query: ExplorationQuery
) -> dict[str, Any]:
    """Align per-episode event volume across runs on one shared episode axis.

    Backend-neutral: each run contributes its own ``query_series`` totals, so any
    mix of backends compares on the same 'events per episode' axis without the
    shell knowing what those events are.
    """
    per_run: list[dict[str, Any]] = []
    domain: set[int] = set()
    for run_id, artifact in artifacts:
        series = query_series(artifact, query)
        totals = dict.fromkeys(series["episodes"], 0)
        for signal in series["series"]:
            for episode, value in zip(series["episodes"], signal["values"]):
                totals[episode] += value
        per_run.append({"id": run_id, "totals": totals, "events": sum(totals.values())})
        domain.update(series["episodes"])
    axis = sorted(domain)
    return {
        "episodes": axis,
        "runs": [
            {
                "id": run["id"],
                "values": [run["totals"].get(episode, 0) for episode in axis],
                "events": run["events"],
            }
            for run in per_run
        ],
    }


def _within_window(row: Mapping[str, Any], query: ExplorationQuery) -> bool:
    """Whether a row's episode falls inside the query's time window."""
    episode = _episode(row)
    if query.start is not None and episode < query.start:
        return False
    return not (query.end is not None and episode > query.end)


def _actor(row: Mapping[str, Any]) -> str:
    return str(row.get("source_user") or "")


def _probe_value(row: Mapping[str, Any]) -> Any:
    data = row.get("data")
    if isinstance(data, Mapping) and "probe_return" in data:
        return data["probe_return"]
    return data
