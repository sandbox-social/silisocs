"""Content-oriented run panels: reconstructed feed and per-agent timeline."""
# ruff: noqa: D101, D102

from __future__ import annotations

import html
from collections import Counter, defaultdict
from typing import Any

from silisocs.analysis.panel import Control, Figure, Html, Panel, register_panel
from silisocs.analysis.panels._shared import episode_of, event_semantics_for_event
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact
from silisocs.visual_tokens import action_color

_EM_DASH = "—"


def _body(text: Any) -> str:
    """Escape post/reply text, rendering an em-dash for empty/missing bodies."""
    if text is None or not str(text).strip():
        return _EM_DASH
    return html.escape(str(text))


def _entry_html(author: str, episode: int, text: Any, css: str) -> str:
    """Render one article header + body (caller closes the ``</article>``)."""
    return (
        f'<article class="{css}">'
        '<header class="feed-author">'
        f"{html.escape(author)}"
        f'<span class="feed-meta">ep {episode}</span>'
        "</header>"
        f'<p class="feed-text">{_body(text)}</p>'
    )


def _render_thread(item: dict[str, Any], children: dict[str, list[dict[str, Any]]]) -> str:
    parts = [_entry_html(item["author"], item["episode"], item["text"], item["css"])]
    nested = children.get(item["id"], [])
    if nested:
        parts.append('<div class="feed-replies">')
        parts.extend(_render_thread(child, children) for child in nested)
        parts.append("</div>")
    parts.append("</article>")
    return "".join(parts)


@register_panel
class ContentFeedPanel(Panel):
    name = "content_feed"
    title = "Content feed"
    scope = "run"
    requires = frozenset({"action_events"})
    controls = (Control(kind="episode_slider", param="episode", label="Episode"),)

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Html:
        assert isinstance(artifact, RunArtifact)
        episode = params.get("episode")
        if episode is not None:
            try:
                episode = int(episode)
            except (TypeError, ValueError):
                episode = None
        limit = max(1, min(int(params.get("limit", 20)), 100))

        roots: list[dict[str, Any]] = []
        children: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in artifact.iter_actions():
            label = str(row.get("label", ""))
            raw_data = row.get("data")
            data = raw_data if isinstance(raw_data, dict) else {}
            semantics = event_semantics_for_event(row, artifact)
            author = str(row.get("source_user", "Unknown"))
            if label in semantics.labels("content.root"):
                content_id = semantics.value(data, "content.id")
                record = {
                    "id": str(content_id or f"root-{len(roots)}"),
                    "author": author,
                    "episode": episode_of(row),
                    "text": semantics.value(data, "content.text"),
                    "css": "feed-post",
                }
                roots.append(record)
            elif label in semantics.labels("content.reply"):
                response_id = semantics.value(data, "content.response_id")
                parent_id = semantics.value(data, "content.parent_id")
                if parent_id is not None:
                    children[str(parent_id)].append(
                        {
                            "id": str(response_id or f"reply-{sum(map(len, children.values()))}"),
                            "author": author,
                            "episode": episode_of(row),
                            "text": semantics.value(data, "content.text"),
                            "css": "feed-reply",
                        }
                    )

        shown = [post for post in roots if episode is None or post["episode"] == episode][:limit]

        parts = ['<div class="feed">']
        if not shown:
            parts.append('<p class="feed-empty">No compatible content events in this view.</p>')
        for post in shown:
            parts.append(_render_thread(post, children))
        parts.append("</div>")
        return Html("".join(parts))


@register_panel
class AgentTimelinePanel(Panel):
    name = "agent_timeline"
    title = "Agent timeline"
    scope = "run"
    requires = frozenset({"action_events"})
    controls = (Control(kind="agent_select", param="agent", label="Agent"),)

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, RunArtifact)
        rows = list(artifact.iter_actions())
        agent = params.get("agent")
        if not agent:
            tally = Counter(str(row.get("source_user", "Unknown")) for row in rows)
            agent = tally.most_common(1)[0][0] if tally else ""
        agent = str(agent)

        by_label: dict[str, list[int]] = defaultdict(list)
        for row in rows:
            if str(row.get("source_user", "Unknown")) == agent:
                by_label[str(row.get("label", "unknown"))].append(episode_of(row))

        traces = []
        for label in sorted(by_label):
            episodes = by_label[label]
            traces.append(
                {
                    "type": "scatter",
                    "mode": "markers",
                    "name": label.replace("_", " ").title(),
                    "x": episodes,
                    "y": [label] * len(episodes),
                    "marker": {
                        "color": action_color(label),
                        "size": 11,
                    },
                }
            )
        return Figure(
            {
                "data": traces,
                "layout": {
                    "title": {"text": f"Timeline · {agent}" if agent else "Timeline"},
                    "xaxis": {"title": "Episode", "dtick": 1},
                    "yaxis": {"title": "", "type": "category"},
                    "hovermode": "closest",
                },
            }
        )
