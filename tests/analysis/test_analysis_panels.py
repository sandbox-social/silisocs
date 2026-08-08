"""Contracts for the shell-independent panel and view language."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from silisocs.analysis.panel import Markdown, Panel, get_panel, output_to_dict, register_panel
from silisocs.analysis.report import render_report
from silisocs.analysis.views import build_view, load_view, parse_view
from silisocs.evaluations.run_artifact import load_run
from silisocs.evaluations.vocabulary import (
    EventSemantics,
    event_semantics_for,
    register_event_semantics,
)


def _run(tmp_path):
    rows = [
        {
            "source_user": "Alice",
            "label": "post",
            "episode": 0,
            "backend_type": "twitter_like",
            "data": {},
        },
        {
            "source_user": "Bob",
            "label": "like",
            "episode": 1,
            "backend_type": "twitter_like",
            "data": {},
        },
    ]
    (tmp_path / "action_events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "scenario": "demo",
                "num_agents": 2,
                "num_steps": 2,
                "game_masters": [{"name": "gm", "backend_type": "twitter_like"}],
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


def test_study_panel_rejects_run_only_declarations():
    """requires/semantics/needs_tags gate run artifacts only; a study panel
    declaring them would get a silently-ignored gate — registration fails loud
    instead.
    """
    with pytest.raises(ValueError, match="run artifacts only"):

        @register_panel
        class _BadStudyPanel(Panel):
            name = "test_bad_study_panel"
            title = "Bad"
            scope = "study"
            requires = frozenset({"action_events"})

            def build(self, artifact, params):
                return Markdown("never built")

    with pytest.raises(KeyError):
        get_panel("test_bad_study_panel")


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


def test_tabs_layout_is_rejected_because_nothing_implements_it(tmp_path):
    """`tabs` was accepted, offered in the composer, and had no CSS behind it —
    it rendered as a grid. A view asking for it now fails loudly instead.
    """
    with pytest.raises(ValueError, match="tabs"):
        parse_view(
            {
                "name": "tabbed",
                "scope": "run",
                "layout": "tabs",
                "panels": [{"built_in": "action_trends"}],
            }
        )
    # The composer must not offer what the validator rejects.
    composer = (
        Path(__file__).resolve().parents[2] / "src/silisocs/studio/templates/scenario.html"
    ).read_text(encoding="utf-8")
    assert "<option>tabs</option>" not in composer


def test_builtin_view_and_report_smoke(tmp_path):
    _run(tmp_path)
    assert load_view("overview").scope == "run"
    document = render_report(tmp_path)
    assert "Run Overview" in document
    assert "data-figure" in document
    assert "Alice" in document


def test_requires_gate_omits_the_panel_and_says_why(tmp_path):
    """A panel whose event stream is absent is left out, not rendered empty."""
    run = _run(tmp_path)  # has action_events, no probe_events
    view = parse_view(
        {"name": "p", "scope": "run", "layout": "rows", "panels": [{"built_in": "probe_trends"}]}
    )
    result = build_view(view, run)
    assert result["panels"] == []
    (skipped,) = result["skipped"]
    assert skipped["name"] == "probe_trends"
    assert "probe_events" in skipped["reason"]


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


def test_condition_comparison_baseline_leads_and_is_labeled(tmp_path):
    """The baseline condition is pinned first and named on the axis."""
    organized = tmp_path / "generated" / "organized"
    organized.mkdir(parents=True)
    (organized / "study_summary.yaml").write_text(
        json.dumps(
            {"metrics_by_condition": {"h1": {"gpt4o-mini": {"rate": 0.4}, "gpt4o": {"rate": 0.1}}}}
        )
    )
    from silisocs.evaluations.run_artifact import load_study

    panel = get_panel("condition_comparison")()
    figure = panel.build(load_study(tmp_path), {"baseline": "gpt4o"}).figure
    (trace,) = figure["data"]
    assert trace["x"] == ["gpt4o (baseline)", "gpt4o-mini"]
    assert trace["y"] == [0.1, 0.4]
    assert figure["layout"]["xaxis"]["title"] == "Condition (baseline: gpt4o)"
    # A typo'd baseline must say so, not silently render the default figure.
    missed = panel.build(load_study(tmp_path), {"baseline": "gtp4o"}).figure
    assert "matches no condition" in missed["layout"]["xaxis"]["title"]


def test_condition_comparison_seed_mode_shows_replicates(tmp_path):
    """compare=seed renders one column per (condition, seed) run record."""
    organized = tmp_path / "generated" / "organized" / "h1"
    organized.mkdir(parents=True)
    (organized / "runs.json").write_text(
        json.dumps(
            [
                {"condition": "gpt4o", "seed": 1, "aggregated": {"rate": 0.1}},
                {"condition": "gpt4o", "seed": 2, "aggregated": {"rate": 0.2}},
                {"condition": "gpt4o-mini", "seed": 1, "aggregated": {"rate": 0.5}},
                {"condition": "broken", "seed": 1, "aggregated": {}},  # no metrics -> dropped
            ]
        )
    )
    from silisocs.evaluations.run_artifact import load_study

    figure = (
        get_panel("condition_comparison")()
        .build(load_study(tmp_path), {"compare": "seed", "baseline": "gpt4o-mini"})
        .figure
    )
    (trace,) = figure["data"]
    assert trace["name"] == "rate"
    # Baseline condition's replicates lead; every replicate is its own column.
    assert trace["x"] == [
        "gpt4o-mini (baseline) · seed 1",
        "gpt4o · seed 1",
        "gpt4o · seed 2",
    ]
    assert trace["y"] == [0.5, 0.1, 0.2]
    assert figure["layout"]["xaxis"]["title"] == "Replicate (baseline: gpt4o-mini)"


def test_event_semantics_registry_is_open_and_namespaced():
    semantics = EventSemantics(
        roles={"content.root": frozenset({"publish"}), "world.transition": {"move"}},
        fields={"content.id": ("object.id",), "world.location": ("destination",)},
        label_tags={"publish": ("content.created", "content.root")},
    )
    register_event_semantics("custom_world", semantics)

    registered = event_semantics_for("custom_world")
    assert registered.labels("world.transition") == frozenset({"move"})
    assert registered.tags_of("publish") == ("content.created", "content.root")
    assert registered.value({"object": {"id": 7}}, "content.id") == 7
    assert event_semantics_for("unknown").labels("content.root") == frozenset()


def test_build_view_isolates_a_failing_panel(tmp_path):
    @register_panel
    class BoomPanel(Panel):
        name = "test_boom"
        title = "Boom"
        scope = "run"

        def build(self, artifact, params):
            raise RuntimeError("kaboom")

    view = parse_view(
        {
            "name": "boomview",
            "scope": "run",
            "layout": "rows",
            "panels": [{"built_in": "test_boom"}, {"built_in": "health_summary"}],
        }
    )
    built = build_view(view, _run(tmp_path))
    # The failing panel renders an error note; the healthy panel still builds.
    assert built["panels"][0]["output"]["type"] == "markdown"
    assert "failed to render" in built["panels"][0]["output"]["text"]
    assert built["panels"][1]["output"]["type"] in {"grid", "figure", "table", "markdown"}


def test_build_view_still_raises_on_unknown_panel(tmp_path):
    view = parse_view(
        {
            "name": "badview",
            "scope": "run",
            "layout": "rows",
            "panels": [{"built_in": "no_such_panel"}],
        }
    )
    with pytest.raises(KeyError):
        build_view(view, _run(tmp_path))
