"""Contracts for the shell-independent panel and view language."""
# ruff: noqa: D103

from __future__ import annotations

import json

import pytest

from silisocs.analysis.panel import Markdown, Panel, get_panel, output_to_dict, register_panel
from silisocs.analysis.report import render_report
from silisocs.analysis.views import build_view, load_view, parse_view
from silisocs.evaluations.run_artifact import load_run
from silisocs.evaluations.vocabulary import (
    ActionVocabulary,
    EventSemantics,
    event_semantics_for,
    register_action_vocabulary,
    register_event_semantics,
    vocabulary_for,
)


def _run(tmp_path):
    rows = [
        {"source_user": "Alice", "label": "post", "episode": 0, "data": {}},
        {"source_user": "Bob", "label": "like", "episode": 1, "data": {}},
    ]
    (tmp_path / "action_events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "scenario": "demo",
                "num_agents": 2,
                "num_steps": 2,
                "artifacts": {"action_events": ["action_events.jsonl"]},
            }
        )
    )
    return load_run(tmp_path)


def test_custom_panel_registration_and_output_shape():
    @register_panel
    class FakePanel(Panel):
        name = "test_fake"
        title = "Fake"
        scope = "run"

        def build(self, artifact, params):
            return Markdown(params.get("text", "ok"))

    assert get_panel("test_fake") is FakePanel
    assert output_to_dict(Markdown("hello")) == {"type": "markdown", "text": "hello"}


def test_view_composes_built_in_panels(tmp_path):
    view = parse_view(
        {
            "name": "small",
            "scope": "run",
            "layout": "rows",
            "panels": [{"built_in": "action_trends"}, {"built_in": "agent_inspector"}],
        }
    )
    result = build_view(view, _run(tmp_path))
    assert [panel["name"] for panel in result["panels"]] == ["action_trends", "agent_inspector"]
    assert result["panels"][0]["output"]["type"] == "figure"
    assert result["panels"][1]["output"]["rows"][0]["agent"] in {"Alice", "Bob"}


def test_builtin_view_and_report_smoke(tmp_path):
    _run(tmp_path)
    assert load_view("overview").scope == "run"
    document = render_report(tmp_path)
    assert "Run Overview" in document
    assert "data-figure" in document
    assert "Alice" in document


def test_requires_gate_replaces_panel_with_placeholder(tmp_path):
    """A panel whose event stream is absent renders a note, not an empty chart."""
    run = _run(tmp_path)  # has action_events, no probe_events
    view = parse_view(
        {"name": "p", "scope": "run", "layout": "rows", "panels": [{"built_in": "probe_trends"}]}
    )
    result = build_view(view, run)
    (panel,) = result["panels"]
    assert panel["missing"] == ["probe_events"]
    assert panel["output"]["type"] == "markdown"
    assert "probe_events" in panel["output"]["text"]


def test_condition_comparison_reads_hypothesis_nested_schema(tmp_path):
    """metrics_by_condition nests conditions under hypothesis ids (docs/study_schema.md)."""
    organized = tmp_path / "generated" / "organized"
    organized.mkdir(parents=True)
    (organized / "study_summary.yaml").write_text(
        json.dumps(
            {
                "metrics_by_condition": {
                    "h1": {"gpt4o-mini": {"self_bleu": 0.45}, "gpt4o": {"self_bleu": 0.04}}
                },
                "metrics_stats_by_condition": {
                    "h1": {
                        "gpt4o-mini": {
                            "self_bleu": {"n": 3, "mean": 0.45, "ci95_low": 0.35, "ci95_high": 0.55}
                        }
                    }
                },
            }
        )
    )
    from silisocs.evaluations.run_artifact import load_study

    figure = get_panel("condition_comparison")().build(load_study(tmp_path), {}).figure
    (trace,) = figure["data"]
    assert trace["name"] == "self_bleu"
    assert trace["x"] == ["gpt4o-mini", "gpt4o"]
    assert trace["y"] == [0.45, 0.04]
    assert trace["error_y"]["array"][0] == pytest.approx(0.1)
    assert trace["error_y"]["arrayminus"][0] == pytest.approx(0.1)


def test_vocabulary_registry_supports_custom_backends():
    vocab = ActionVocabulary(creates_content=frozenset({"publish"}))
    register_action_vocabulary("custom", vocab)
    assert vocabulary_for("custom") is vocab
    assert vocabulary_for("unknown").interactions == frozenset()


def test_event_semantics_registry_is_open_and_namespaced():
    semantics = EventSemantics(
        roles={"content.root": frozenset({"publish"}), "world.transition": {"move"}},
        fields={"content.id": ("object.id",), "world.location": ("destination",)},
    )
    register_event_semantics("custom_world", semantics)

    registered = event_semantics_for("custom_world")
    assert registered.labels("world.transition") == frozenset({"move"})
    assert registered.value({"object": {"id": 7}}, "content.id") == 7
    assert event_semantics_for("unknown").labels("content.root") == frozenset()
