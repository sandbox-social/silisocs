"""Contract tests for the interaction-network run panel."""
# ruff: noqa: D103

from __future__ import annotations

import html
import json
import re

from silisocs.analysis.panel import Html, get_panel
from silisocs.analysis.panels.network import InteractionNetworkPanel
from silisocs.evaluations.run_artifact import load_run
from silisocs.visual_tokens import ACTION_COLORS

# Rows copy the real twitter_like action-event shapes (verified against
# src/.../twitter_like/app.py and a real action_events.jsonl):
#   post  -> data {post_id, post_text}           (creates post; owner = source)
#   reply -> data {post_id, reply_to_id, ...}    (targets reply_to_id's owner)
#   like  -> data {post_id}                      (targets post_id's owner)
#   follow-> data {target_user}                  (edge source -> target_user)
_ROWS = [
    {
        "source_user": "Alice",
        "label": "post",
        "episode": 0,
        "data": {"post_id": "1", "post_text": "hi"},
    },
    {"source_user": "Bob", "label": "follow", "episode": 0, "data": {"target_user": "Alice"}},
    {
        "source_user": "Bob",
        "label": "reply",
        "episode": 1,
        "data": {"post_id": "2", "reply_to_id": "1", "post_text": "re"},
    },
    {"source_user": "Carol", "label": "like", "episode": 1, "data": {"post_id": "1"}},
]


def _make_run(tmp_path, rows):
    lines = "".join(json.dumps(row) + "\n" for row in rows)
    (tmp_path / "action_events.jsonl").write_text(lines)
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"status": "success", "artifacts": {"action_events": ["action_events.jsonl"]}})
    )
    return load_run(tmp_path)


def _payload(output: Html) -> dict:
    match = re.search(r'data-cy-graph="(.*)"></div>', output.html, flags=re.DOTALL)
    assert match is not None, output.html
    return json.loads(html.unescape(match.group(1)))


def _nodes(payload: dict) -> set[str]:
    return {el["data"]["id"] for el in payload["elements"] if "source" not in el["data"]}


def _edges(payload: dict) -> list[dict]:
    return [el for el in payload["elements"] if "source" in el["data"]]


def test_network_panel_is_registered():
    assert get_panel("interaction_network") is InteractionNetworkPanel


def test_network_builds_nodes_and_resolved_edges(tmp_path):
    run = _make_run(tmp_path, _ROWS)
    output = InteractionNetworkPanel().build(run, {})
    assert isinstance(output, Html)

    payload = _payload(output)
    assert {"Alice", "Bob", "Carol"} <= _nodes(payload)

    edges = _edges(payload)
    reply_edges = [
        e
        for e in edges
        if e.get("classes") == "interaction"
        and e["data"]["source"] == "Bob"
        and e["data"]["target"] == "Alice"
    ]
    assert reply_edges and reply_edges[0]["data"]["color"] == ACTION_COLORS["reply"]

    follow_edges = [
        e
        for e in edges
        if e.get("classes") == "follow"
        and e["data"]["source"] == "Bob"
        and e["data"]["target"] == "Alice"
    ]
    assert follow_edges

    # Every node element carries a numeric preset position.
    for el in payload["elements"]:
        if "source" not in el["data"]:
            assert isinstance(el["position"]["x"], (int, float))
            assert isinstance(el["position"]["y"], (int, float))


def test_episode_filter_drops_interactions_but_keeps_follows(tmp_path):
    run = _make_run(tmp_path, _ROWS)
    payload = _payload(InteractionNetworkPanel().build(run, {"episode": 0}))
    edges = _edges(payload)
    kinds = {e.get("classes") for e in edges}
    assert "interaction" not in kinds  # reply/like are episode 1
    assert any(e.get("classes") == "follow" for e in edges)  # follow (episode 0) stays


def test_empty_run_yields_html_without_raising(tmp_path):
    (tmp_path / "action_events.jsonl").write_text("")
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"status": "success", "artifacts": {"action_events": ["action_events.jsonl"]}})
    )
    output = InteractionNetworkPanel().build(load_run(tmp_path), {})
    assert isinstance(output, Html)
    payload = _payload(output)
    assert payload["elements"] == []
