"""Capability-driven interaction network with deterministic Python layout.

The panel knows semantic roles such as ``content.root``,
``interaction.reaction``, and ``network.follow``; backend packages map their
own labels and payload paths to those open roles. Layout is baked into a
CSP-friendly Cytoscape payload so shells never need backend-specific code.
"""
# ruff: noqa: D101, D102

from __future__ import annotations

import html
import json
import math
from typing import Any

from silisocs.analysis.inputs import Event, event_frame
from silisocs.analysis.panel import Control, Html, Panel, register_panel
from silisocs.design.tokens import HIGHLIGHT, action_color
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact

_CANVAS = 600.0
_PAD = 40.0  # keep every node inside ~[_PAD, _CANVAS - _PAD]


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


# Content owners are stored in separate root/reply namespaces. Backends with one
# shared ID space resolve across both; typed targets resolve only within the
# namespace their target kind names, avoiding collisions between independent ID
# spaces.
_OwnerMap = dict[tuple[str, str, str], str]


def _owner_any(owner: _OwnerMap, backend_type: str, key: Any) -> str | None:
    """Look an id up in either content namespace (single-id-space backends)."""
    if key is None:
        return None
    ident = str(key)
    return owner.get((backend_type, "root", ident)) or owner.get((backend_type, "reply", ident))


def _reaction_target(event: Event, owner: _OwnerMap) -> str | None:
    """Resolve the author a reaction (like/upvote/...) is directed at.

    When the backend names a typed target (``interaction.target_type`` +
    ``interaction.target_id``, e.g. reddit's ``post``/``comment`` votes), resolve
    against exactly that namespace. Otherwise the reaction addresses a shared ID
    space through ``content.id``.
    """
    target_type = event.field("interaction.target_type")
    if target_type is not None:
        target_id = event.field("interaction.target_id")
        if target_id is None:
            return None
        reply_kind = str(target_type) in event.labels_for("content.reply")
        namespace = "reply" if reply_kind else "root"
        return owner.get((event.backend_type, namespace, str(target_id)))
    return _owner_any(owner, event.backend_type, event.field("content.id"))


def _interaction_target(
    event: Event,
    owner: _OwnerMap,
    *,
    is_reaction: bool,
) -> str | None:
    """Resolve the author a reply or reaction is directed at."""
    if is_reaction:
        return _reaction_target(event, owner)
    return _owner_any(owner, event.backend_type, event.field("content.parent_id"))


def _coerce_episode(value: Any) -> int | None:
    """None/absent -> all interactions; an int-like value -> that episode only."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scale_positions(raw: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    """Normalize arbitrary float coords into the ~[_PAD, _CANVAS - _PAD] box."""
    if not raw:
        return {}
    xs = [x for x, _ in raw.values()]
    ys = [y for _, y in raw.values()]
    span = _CANVAS - 2 * _PAD

    def _axis(value: float, lo: float, hi: float) -> float:
        if hi <= lo:
            return _CANVAS / 2
        return _PAD + (value - lo) / (hi - lo) * span

    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    return {
        name: (round(_axis(x, min_x, max_x), 2), round(_axis(y, min_y, max_y), 2))
        for name, (x, y) in raw.items()
    }


def _circle_layout(nodes: list[str]) -> dict[str, tuple[float, float]]:
    center = _CANVAS / 2
    if not nodes:
        return {}
    if len(nodes) == 1:
        return {nodes[0]: (center, center)}
    radius = _CANVAS / 2 - _PAD
    positions: dict[str, tuple[float, float]] = {}
    for index, name in enumerate(nodes):
        angle = 2 * math.pi * index / len(nodes)
        positions[name] = (
            round(center + radius * math.cos(angle), 2),
            round(center + radius * math.sin(angle), 2),
        )
    return positions


def _layout(
    nodes: list[str], edges: list[tuple[str, str]], layout_name: str
) -> dict[str, tuple[float, float]]:
    """Deterministic layout in Python: spring layout, else evenly-spaced circle."""
    if layout_name == "circle":
        return _circle_layout(nodes)
    try:
        import networkx as nx

        graph = nx.Graph()
        graph.add_nodes_from(nodes)
        graph.add_edges_from((s, t) for s, t in edges if s != t)
        raw = {
            name: (float(x), float(y)) for name, (x, y) in nx.spring_layout(graph, seed=42).items()
        }
        return _scale_positions(raw)
    except ImportError:
        return _circle_layout(nodes)


@register_panel
class InteractionNetworkPanel(Panel):
    name = "interaction_network"
    # Any edge-forming role is enough to draw a graph: follows, reactions,
    # replies, or a direct agent-to-agent interaction on a non-social backend.
    semantics = frozenset(
        {"network.follow", "interaction.reaction", "content.reply", "interaction.directed"}
    )
    title = "Interaction network"
    scope = "run"
    requires = frozenset({"action_events"})
    controls = (
        Control(kind="episode_slider", param="episode", label="Episode"),
        Control(kind="select", param="layout", label="Layout", choices=("force", "circle")),
        Control(kind="agent_select", param="highlight", label="Highlight"),
    )

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Html:
        assert isinstance(artifact, RunArtifact)
        episode = _coerce_episode(params.get("episode"))
        layout_name = str(params.get("layout") or "force")
        highlight = str(params.get("highlight") or "")

        nodes: set[str] = set()
        owner: _OwnerMap = {}
        follow_edges: list[tuple[str, str]] = []
        interaction_edges: list[tuple[str, str, str]] = []  # (source, target, color)

        for event in event_frame(artifact):
            label = event.label
            source = _str_or_none(event.actor)
            if source is not None:
                nodes.add(source)

            if "content.root" in event.tags and source is not None:
                created = event.field("content.id")
                if created is not None:
                    owner[(event.backend_type, "root", str(created))] = source
            elif "content.reply" in event.tags and source is not None:
                created = event.field("content.response_id")
                if created is not None:
                    owner[(event.backend_type, "reply", str(created))] = source

            if "network.follow" in event.tags:
                target = _str_or_none(event.field("network.target_actor"))
                if source is not None and target is not None:
                    nodes.add(target)
                    follow_edges.append((source, target))
                continue

            # A directed interaction names its target actor outright, so it needs
            # no content to resolve through — the seam a non-social backend uses
            # to put its agent-to-agent interactions on this graph.
            if "interaction.directed" in event.tags and source is not None:
                if episode is not None and event.episode != episode:
                    continue
                target = _str_or_none(event.field("network.target_actor"))
                if target is not None:
                    nodes.add(target)
                    interaction_edges.append((source, target, action_color(label)))
                continue

            is_reply = "content.reply" in event.tags
            is_reaction = "interaction.reaction" in event.tags
            if (is_reply or is_reaction) and source is not None:
                if episode is not None and event.episode != episode:
                    continue
                target = _interaction_target(event, owner, is_reaction=is_reaction)
                if target is None:  # unresolved target -> skip (self-loops otherwise allowed)
                    continue
                color = action_color(label)
                interaction_edges.append((source, target, color))

        node_list = sorted(nodes)
        degree: dict[str, int] = dict.fromkeys(node_list, 0)
        for source, target in follow_edges:
            degree[source] = degree.get(source, 0) + 1
            degree[target] = degree.get(target, 0) + 1
        for source, target, _ in interaction_edges:
            degree[source] = degree.get(source, 0) + 1
            degree[target] = degree.get(target, 0) + 1

        positions = _layout(
            node_list,
            [(s, t) for s, t in follow_edges] + [(s, t) for s, t, _ in interaction_edges],
            layout_name,
        )

        elements: list[dict[str, Any]] = []
        for name in node_list:
            x, y = positions.get(name, (_CANVAS / 2, _CANVAS / 2))
            size = min(48, 18 + 3 * degree.get(name, 0))  # modest degree scaling
            elements.append(
                {
                    "data": {"id": name, "label": name, "size": size},
                    "position": {"x": x, "y": y},
                    "classes": "highlighted" if name == highlight else "",
                }
            )
        for index, (source, target) in enumerate(follow_edges):
            elements.append(
                {
                    "data": {"id": f"follow-{index}", "source": source, "target": target},
                    "classes": "follow",
                }
            )
        for index, (source, target, color) in enumerate(interaction_edges):
            elements.append(
                {
                    "data": {
                        "id": f"int-{index}",
                        "source": source,
                        "target": target,
                        "color": color,
                    },
                    "classes": "interaction",
                }
            )

        payload = {"elements": elements, "style": _stylesheet()}
        # html.escape(quote=True) both attribute-escapes the JSON and turns every
        # ``<`` into ``&lt;`` — that alone neutralizes a ``</script>`` breakout, and
        # html.unescape reverses it exactly (so consumers json.loads it back cleanly).
        encoded = html.escape(json.dumps(payload, separators=(",", ":")), quote=True)
        return Html(f'<div class="cy-network" data-cy-graph="{encoded}"></div>')


def _stylesheet() -> list[dict[str, Any]]:
    """Cytoscape stylesheet with theme-dependent colors expressed as CSS variables.

    Cytoscape cannot resolve ``var(--x)`` itself, so shells substitute those
    values from the active theme before init (and again on theme toggle) —
    the same shell-owned theming contract Plotly figures use. Categorical
    action colors are theme-independent and stay literal.
    """
    return [
        {
            "selector": "node",
            "style": {
                "background-color": "var(--accent)",
                "label": "data(label)",
                "font-size": 9,
                "text-valign": "bottom",
                "color": "var(--muted)",
                "width": "data(size)",
                "height": "data(size)",
            },
        },
        {
            "selector": "edge.follow",
            "style": {
                "line-color": "var(--border)",
                "width": 1,
                "curve-style": "bezier",
                "target-arrow-shape": "none",
            },
        },
        {
            "selector": "node.highlighted",
            "style": {"border-width": 4, "border-color": HIGHLIGHT},
        },
        {
            "selector": "edge.interaction",
            "style": {
                "line-color": "data(color)",
                "width": 2,
                "target-arrow-shape": "triangle",
                "target-arrow-color": "data(color)",
                "curve-style": "bezier",
            },
        },
    ]
