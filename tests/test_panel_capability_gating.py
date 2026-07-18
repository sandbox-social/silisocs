"""A run only shows the panels its backend can feed.

Panels declare the semantic roles they read; backends declare the roles they
populate. Everything else — the view, the run nav, the composer's view builder —
follows from comparing the two, with no surface naming a backend.
"""
# ruff: noqa: D103

from __future__ import annotations

import json

import pytest

from silisocs.analysis.panel import Panel, get_panel
from silisocs.analysis.panels._shared import run_has_tags, run_semantic_roles
from silisocs.analysis.views import build_view, load_view, skip_reason, view_applies
from silisocs.evaluations.run_artifact import load_run
from silisocs.evaluations.vocabulary import event_semantics_for
from silisocs.studio.capabilities import backend_panel_names, panel_supports

_MARKET_EVENTS = [
    {
        "source_user": "Alex",
        "label": "produce_resource",
        "episode": 0,
        "backend_type": "resource_market",
        "data": {"resource": "food", "quantity": 2},
    },
    {
        "source_user": "Blair",
        "label": "list_resource",
        "episode": 0,
        "backend_type": "resource_market",
        "data": {"listing_id": 1, "resource": "wood", "quantity": 3, "price": 5},
    },
    {
        "source_user": "Alex",
        "label": "buy_listing",
        "episode": 1,
        "backend_type": "resource_market",
        "data": {
            "listing_id": 1,
            "resource": "wood",
            "quantity": 3,
            "price": 5,
            "target_user": "Blair",
        },
    },
]

_SOCIAL_EVENTS = [
    {
        "source_user": "alice",
        "label": "post",
        "episode": 0,
        "backend_type": "twitter_like",
        "data": {"post_id": 1, "post_text": "hello"},
    },
    {
        "source_user": "bob",
        "label": "reply",
        "episode": 0,
        "backend_type": "twitter_like",
        "data": {"post_id": 2, "reply_to_id": 1, "post_text": "hi"},
    },
]


def _make_run(root, backend_type, events, name="run"):
    run = root / name
    run.mkdir(parents=True)
    (run / "action_events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "scenario": "demo",
                "game_masters": [{"name": "gm", "backend_type": backend_type}],
                "artifacts": {"action_events": ["action_events.jsonl"]},
            }
        )
    )
    return load_run(run)


@pytest.fixture
def market_run(tmp_path):
    return _make_run(tmp_path, "resource_market", _MARKET_EVENTS, name="market")


@pytest.fixture
def social_run(tmp_path):
    return _make_run(tmp_path, "twitter_like", _SOCIAL_EVENTS, name="social")


def test_run_semantic_roles_come_from_the_backends_declaration(market_run, social_run):
    assert run_semantic_roles(market_run) == frozenset(
        {"control", "market.inspect", "market.listing", "market.trade", "market.stock"}
    )
    assert {"content.root", "network.follow"} <= run_semantic_roles(social_run)


def test_a_run_whose_backend_declares_nothing_has_no_roles(tmp_path):
    run = _make_run(tmp_path, "unknown_backend", _MARKET_EVENTS, name="opaque")
    assert run_semantic_roles(run) == frozenset()
    assert not run_has_tags(run)


@pytest.mark.parametrize(
    ("panel_name", "in_market", "in_social"),
    [
        ("market_activity", True, False),
        ("market_ledger", True, False),
        ("content_feed", False, True),
        ("interaction_network", False, True),
        # Backend-neutral panels apply to both.
        ("action_trends", True, True),
        ("agent_inspector", True, True),
        ("agent_timeline", True, True),
        ("health_summary", True, True),
    ],
)
def test_panels_apply_only_where_their_semantics_are_declared(
    market_run, social_run, panel_name, in_market, in_social
):
    panel = get_panel(panel_name)
    assert panel.applicable(market_run) is in_market
    assert panel.applicable(social_run) is in_social


def test_behavior_breakdown_needs_open_tags(market_run, tmp_path):
    panel = get_panel("behavior_breakdown")
    assert panel.needs_tags and not panel.semantics
    assert panel.applicable(market_run)
    opaque = _make_run(tmp_path, "unknown_backend", _MARKET_EVENTS, name="opaque")
    assert not panel.applicable(opaque)


def test_action_alignment_needs_suggestions_in_the_data(tmp_path, social_run):
    # No shipped backend records suggestions, so it stays hidden by default.
    assert not get_panel("action_alignment").applicable(social_run)
    with_suggestions = _make_run(
        tmp_path,
        "twitter_like",
        [{**_SOCIAL_EVENTS[0], "data": {"post_id": 1, "suggested_action": "like"}}],
        name="recsys",
    )
    assert get_panel("action_alignment").applicable(with_suggestions)


def test_skip_reason_names_the_gate_that_actually_failed(market_run, tmp_path):
    assert skip_reason(get_panel("market_activity"), market_run) == ""
    # A missing event stream, a missing semantic role, and missing action tags
    # are different gaps, and the reason says which.
    assert "probe_events" in skip_reason(get_panel("probe_trends"), market_run)
    assert skip_reason(get_panel("content_feed"), market_run) == (
        "needs content.reply, content.root — not declared by this run's backend"
    )
    opaque = _make_run(tmp_path, "unknown_backend", _MARKET_EVENTS, name="opaque")
    assert skip_reason(get_panel("behavior_breakdown"), opaque) == (
        "this run's backend declares no action tags"
    )
    # action_alignment gates on run data, not on a declaration.
    assert skip_reason(get_panel("action_alignment"), market_run) == "nothing in this run to show"


def test_build_view_omits_inapplicable_panels_and_reports_them(market_run):
    built = build_view(load_view("content"), market_run)
    rendered = {panel["name"] for panel in built["panels"]}
    skipped = {panel["name"]: panel["reason"] for panel in built["skipped"]}
    assert "content_feed" not in rendered  # no placeholder card either
    assert "not declared by this run's backend" in skipped["content_feed"]
    assert "recent_events" in rendered  # generic panels still build


def test_views_are_offered_only_where_their_subject_applies(market_run, social_run):
    def offered(artifact):
        return {
            name
            for name in ("overview", "network", "content", "market", "cost")
            if view_applies(load_view(name), artifact)
        }

    assert offered(market_run) == {"overview", "market", "cost"}
    assert offered(social_run) == {"overview", "network", "content", "cost"}


def test_market_panels_read_declared_fields_not_backend_names(market_run):
    figure = get_panel("market_activity")().build(market_run, {})
    traded = {trace["name"]: trace["y"] for trace in figure.figure["data"]}
    assert traded == {"wood": [3]}  # only the buy_listing trade, in episode 1

    value = get_panel("market_activity")().build(market_run, {"measure": "value"})
    assert {trace["name"]: trace["y"] for trace in value.figure["data"]} == {"wood": [5]}


def test_market_ledger_attributes_flow_to_agents(market_run):
    grid = get_panel("market_ledger")().build(market_run, {})
    table = grid.items[0]
    rows = {row["agent"]: row for row in table.rows}
    assert rows["Alex"]["produced/consumed"] == 2
    assert rows["Blair"]["listed"] == 3
    assert rows["Alex"]["traded"] == 3
    flows = grid.items[2]
    assert flows.rows == [{"from": "Alex", "to": "Blair", "units": 3}]


def test_composer_offers_only_panels_the_chosen_backend_can_feed():
    """The same gate applies before a run exists, from the backend class alone."""
    from silisocs.environments.backends.factory import resolve_backend_class

    panels = [get_panel(name) for name in ("market_activity", "content_feed", "action_trends")]
    market = backend_panel_names(
        "resource_market", resolve_backend_class("resource_market"), panels
    )
    social = backend_panel_names("twitter_like", resolve_backend_class("twitter_like"), panels)
    assert market == ("market_activity", "action_trends")
    assert social == ("content_feed", "action_trends")


def test_a_custom_backend_declares_capabilities_on_its_own_class():
    """No central registration: the class is the declaration."""

    class LedgerWorld:
        event_semantics = {
            "roles": {"market.trade": ["settle"]},
            "fields": {"market.resource": ["asset"]},
        }

    panels = [get_panel(name) for name in ("market_activity", "content_feed")]
    assert backend_panel_names("bespoke_ledger", LedgerWorld, panels) == ("market_activity",)


def test_semantics_resolve_registered_and_decorator_derived_tags():
    # Registered centrally (shared social shape).
    assert event_semantics_for("twitter_like").labels("content.root")
    assert event_semantics_for("twitter_like").tags_of("post")[0] == "creates_content"
    # Derived from decorators on the backend class, never registered.
    assert event_semantics_for("resource_market").labels("market.trade")
    assert event_semantics_for("resource_market").tags_of("inspect_market") == ("market.inspect",)
    # Unknown backends resolve to empty capabilities rather than raising.
    assert event_semantics_for("nope").roles == {}


def test_panel_supports_reads_declarations_not_panel_names():
    """A third-party panel is judged by the same declarations as a built-in."""

    class Roleless(Panel):
        name = "roleless"
        title = "Roleless"
        scope = "run"

        def build(self, artifact, params):  # pragma: no cover - never built here
            raise AssertionError

    class Grouped(Roleless):
        name = "grouped"
        needs_tags = True

    assert panel_supports(Roleless, frozenset())
    assert not panel_supports(Grouped, frozenset())
    assert panel_supports(Grouped, frozenset({"custom.tag"}))
    assert panel_supports(get_panel("market_activity"), frozenset({"market.trade"}))
    assert not panel_supports(get_panel("market_activity"), frozenset({"content.root"}))
