"""Tests for the content, behavior, and metrics analysis panels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from silisocs.analysis.panel import Figure, Grid, Html, Markdown, Panel, list_panels
from silisocs.analysis.panels.behavior import BehaviorBreakdownPanel
from silisocs.analysis.panels.content import AgentTimelinePanel, ContentFeedPanel
from silisocs.analysis.panels.metrics import TokenUsagePanel
from silisocs.evaluations.run_artifact import load_run
from silisocs.evaluations.vocabulary import EventSemantics, register_event_semantics


def _post(source: str, post_id: str, text: str, episode: int) -> dict[str, Any]:
    return {
        "source_user": source,
        "label": "post",
        "data": {"post_id": post_id, "post_text": text},
        "episode": episode,
    }


def _reply(source: str, post_id: str, reply_to_id: str, text: str, episode: int) -> dict[str, Any]:
    return {
        "source_user": source,
        "label": "reply",
        "data": {"post_id": post_id, "reply_to_id": reply_to_id, "post_text": text},
        "episode": episode,
    }


def _simple(source: str, label: str, episode: int) -> dict[str, Any]:
    return {"source_user": source, "label": label, "data": {"post_id": "1"}, "episode": episode}


def _write_run(
    run_dir: Path,
    events: list[dict[str, Any]],
    *,
    game_masters: list[dict[str, Any]] | None = None,
    llm_usage: dict[str, Any] | None = None,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "action_events.jsonl").open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")
    manifest: dict[str, Any] = {"status": "success"}
    if game_masters is not None:
        manifest["game_masters"] = game_masters
    if llm_usage is not None:
        manifest["llm_usage"] = llm_usage
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir


def test_content_feed_renders_post_and_nested_escaped_reply(tmp_path: Path) -> None:
    events = [
        _post("Alex", "1", "hello <script>alert(1)</script> & friends", 0),
        _reply("Casey", "3", "1", "great <b>point</b>", 1),
        _post("Dana", "2", "", 0),  # empty body -> em-dash
    ]
    run = _write_run(tmp_path / "run", events)
    output = ContentFeedPanel().build(load_run(run), {})

    assert isinstance(output, Html)
    body = output.html
    assert '<article class="feed-post">' in body
    assert '<div class="feed-replies">' in body
    assert '<article class="feed-reply">' in body
    # The <script> is escaped, never emitted raw.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>" not in body
    assert "&amp; friends" in body
    assert "&lt;b&gt;point&lt;/b&gt;" in body
    # Empty post still renders with an em-dash body.
    assert "—" in body


def test_content_feed_episode_filter_keeps_replies(tmp_path: Path) -> None:
    events = [
        _post("Alex", "1", "ep0 post", 0),
        _reply("Casey", "3", "1", "reply from ep1", 1),
        _post("Dana", "2", "ep1 post", 1),
    ]
    run = _write_run(tmp_path / "run", events)
    artifact = load_run(run)

    ep0 = ContentFeedPanel().build(artifact, {"episode": 0}).html
    assert "ep0 post" in ep0
    assert "ep1 post" not in ep0
    # A shown post keeps its replies even when the reply is from another episode.
    assert "reply from ep1" in ep0

    ep1 = ContentFeedPanel().build(artifact, {"episode": 1}).html
    assert "ep1 post" in ep1
    assert "ep0 post" not in ep1


def test_content_feed_limit_caps_posts(tmp_path: Path) -> None:
    events = [_post("Alex", str(i), f"post {i}", 0) for i in range(5)]
    run = _write_run(tmp_path / "run", events)
    output = ContentFeedPanel().build(load_run(run), {"limit": 2})
    assert output.html.count('<article class="feed-post">') == 2


def test_content_feed_supports_registered_custom_backend_semantics(tmp_path: Path) -> None:
    register_event_semantics(
        "custom_world",
        EventSemantics(
            roles={"content.root": {"announce"}, "content.reply": {"respond"}},
            fields={
                "content.id": ("entity.key",),
                "content.response_id": ("response_key",),
                "content.parent_id": ("parent_key",),
                "content.text": ("message",),
            },
        ),
    )
    events = [
        {
            "source_user": "A",
            "backend_type": "custom_world",
            "label": "announce",
            "episode": 0,
            "data": {"entity": {"key": "root"}, "message": "hello"},
        },
        {
            "source_user": "B",
            "backend_type": "custom_world",
            "label": "respond",
            "episode": 0,
            "data": {"response_key": "r1", "parent_key": "root", "message": "hi"},
        },
    ]
    run = _write_run(
        tmp_path / "custom",
        events,
        game_masters=[{"name": "world", "backend_type": "custom_world"}],
    )
    output = ContentFeedPanel().build(load_run(run), {})
    assert "hello" in output.html
    assert "hi" in output.html


def test_agent_timeline_defaults_to_most_active_agent(tmp_path: Path) -> None:
    events = [
        _post("Alex", "1", "a", 0),
        _post("Alex", "2", "b", 1),
        _simple("Alex", "like", 1),
        _post("Casey", "3", "c", 0),
    ]
    run = _write_run(tmp_path / "run", events)
    output = AgentTimelinePanel().build(load_run(run), {})

    assert isinstance(output, Figure)
    assert "Alex" in output.figure["layout"]["title"]["text"]
    # Only Alex's actions (post + like) become traces; Casey excluded.
    labels = {trace["name"] for trace in output.figure["data"]}
    assert labels == {"Post", "Like"}
    assert output.figure["layout"]["xaxis"]["dtick"] == 1
    assert output.figure["layout"]["hovermode"] == "closest"


def test_agent_timeline_explicit_agent(tmp_path: Path) -> None:
    events = [
        _post("Alex", "1", "a", 0),
        _post("Alex", "2", "b", 1),
        _post("Casey", "3", "c", 0),
    ]
    run = _write_run(tmp_path / "run", events)
    output = AgentTimelinePanel().build(load_run(run), {"agent": "Casey"})

    assert "Casey" in output.figure["layout"]["title"]["text"]
    posts = [trace for trace in output.figure["data"] if trace["name"] == "Post"]
    assert len(posts) == 1
    assert posts[0]["x"] == [0]  # only Casey's single episode-0 post


def test_behavior_breakdown_groups_twitter_actions(tmp_path: Path) -> None:
    events = [
        _post("Alex", "1", "a", 0),  # creates_content
        _reply("Casey", "3", "1", "r", 0),  # creates_content
        _simple("Dana", "like", 0),  # endorses
        _simple("Dana", "follow", 1),  # social_graph
    ]
    run = _write_run(
        tmp_path / "run",
        events,
        game_masters=[{"name": "gm", "backend_type": "twitter_like"}],
    )
    output = BehaviorBreakdownPanel().build(load_run(run), {})

    assert isinstance(output, Figure)
    assert output.figure["layout"]["barmode"] == "stack"
    traces = {trace["name"]: trace for trace in output.figure["data"]}
    assert set(traces) == {"Creates Content", "Endorses", "Social Graph"}
    x = output.figure["data"][0]["x"]
    creates = dict(zip(x, traces["Creates Content"]["y"]))
    assert creates[0] == 2  # post + reply both in episode 0
    endorses = dict(zip(x, traces["Endorses"]["y"]))
    assert endorses[0] == 1
    social = dict(zip(x, traces["Social Graph"]["y"]))
    assert social[1] == 1


def test_behavior_breakdown_unknown_backend_is_other(tmp_path: Path) -> None:
    events = [_post("Alex", "1", "a", 0), _simple("Alex", "like", 0)]
    run = _write_run(
        tmp_path / "run",
        events,
        game_masters=[{"name": "gm", "backend_type": "mystery_backend"}],
    )
    output = BehaviorBreakdownPanel().build(load_run(run), {})

    names = {trace["name"] for trace in output.figure["data"]}
    assert names == {"Other"}
    other = output.figure["data"][0]
    assert other["y"] == [2]
    assert other["marker"]["color"] == "#b9c2c6"


def test_token_usage_per_model_figure(tmp_path: Path) -> None:
    usage = {
        "per_model": [
            {
                "model": "gpt-4o-mini",
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            }
        ],
        "totals": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        "pricing_applied": False,
    }
    run = _write_run(tmp_path / "run", [_post("Alex", "1", "a", 0)], llm_usage=usage)
    output = TokenUsagePanel().build(load_run(run), {})

    assert isinstance(output, Figure)
    assert output.figure["layout"]["barmode"] == "group"
    names = {trace["name"] for trace in output.figure["data"]}
    assert names == {"Prompt tokens", "Completion tokens"}
    assert "title" not in output.figure["layout"]
    # No cost field present -> no cost annotation invented.
    assert "annotations" not in output.figure["layout"]


def test_token_usage_totals_only_grid(tmp_path: Path) -> None:
    usage = {"totals": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}}
    run = _write_run(tmp_path / "run", [_post("Alex", "1", "a", 0)], llm_usage=usage)
    output = TokenUsagePanel().build(load_run(run), {})

    assert isinstance(output, Grid)
    text = " ".join(item.text for item in output.items if isinstance(item, Markdown))
    assert "60" in text
    assert "Total tokens" in text
    # No cost field -> no cost tile.
    assert "cost" not in text.lower()


def test_token_usage_missing_is_placeholder_grid(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "run", [_post("Alex", "1", "a", 0)])
    output = TokenUsagePanel().build(load_run(run), {})

    assert isinstance(output, Grid)
    assert len(output.items) == 1
    placeholder = output.items[0]
    assert isinstance(placeholder, Markdown)
    assert "No usage recorded" in placeholder.text


def test_all_four_panels_registered() -> None:
    names = {panel.name for panel in list_panels()}
    assert {"content_feed", "agent_timeline", "behavior_breakdown", "token_usage"} <= names


@pytest.mark.parametrize(
    "panel_cls",
    [ContentFeedPanel, AgentTimelinePanel, BehaviorBreakdownPanel, TokenUsagePanel],
)
def test_panels_declare_run_scope(panel_cls: type[Panel]) -> None:
    assert panel_cls.scope == "run"
