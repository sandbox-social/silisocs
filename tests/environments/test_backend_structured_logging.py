"""Structured action logging belongs to every backend, not just social ones.

``_log_action_event`` records who did what with which payload — the structure the
generic panels group and attribute by. It lives on ``BackendApp`` so a non-social
backend is not stuck emitting anonymous narration.
"""
# ruff: noqa: D103

from __future__ import annotations

from typing import Any

import pytest

from silisocs.environments.backends.base import (
    ActionResult,
    BackendApp,
    SocialBackendApp,
    app_action,
)
from silisocs.environments.backends.resource_market.app import ResourceMarketApp
from silisocs.environments.backends.virtual_space.app import VirtualSpaceApp
from silisocs.environments.gm.components.resolve import GenericActionResolveComponent
from silisocs.runtime.checkpointing.restore import _replay_event_fields
from silisocs.runtime.types import ActionOutput


class _RecordingLogger:
    """Stands in for the run's JSONL event logger."""

    def __init__(self, episode_idx: int = 0) -> None:
        self.episode_idx = episode_idx
        self.rows: list[dict[str, Any]] = []

    def log(self, row: dict[str, Any]) -> None:
        self.rows.append(row)


def test_structured_logging_is_available_to_every_backend():
    assert BackendApp._log_action_event is SocialBackendApp._log_action_event
    for name in ("iter_committed_events", "count_committed_events", "_committed_event_log"):
        assert hasattr(BackendApp, name)


def test_committed_mirror_tracks_the_log_on_a_plain_backend():
    class Counter(BackendApp):
        def name(self) -> str:
            return "counter"

        def description(self) -> str:
            return "test backend"

    app = Counter()
    app.action_logger = _RecordingLogger(episode_idx=2)
    app._log_action_event("Alex", "tick", {"value": 1})

    assert app.action_logger.rows == [
        {"source_user": "Alex", "label": "tick", "data": {"value": 1}}
    ]
    assert app.count_committed_events(labels=["tick"]) == 1
    assert app.count_committed_events(agent="Blair") == 0
    (event,) = app.iter_committed_events()
    assert event == {"label": "tick", "source_user": "Alex", "episode": 2, "data": {"value": 1}}


@pytest.fixture
def market():
    app = ResourceMarketApp(
        production_capabilities={"farmer": {"food": 3}},
        role_needs={},
    )
    app.action_logger = _RecordingLogger()
    app.initialize(agent_names=["Alex", "Blair"], sim_roles={"Alex": "farmer"})
    return app


def test_market_actions_log_actor_label_and_payload(market):
    market.invoke_action_detailed(
        "produce_resource", {"agent_name": "Alex", "resource": "food", "quantity": 2}
    )
    market.invoke_action_detailed(
        "list_resource",
        {"agent_name": "Alex", "resource": "food", "quantity": 2, "price": 5},
    )
    market.invoke_action_detailed("buy_listing", {"agent_name": "Blair", "listing_id": 1})

    rows = [row for row in market.action_logger.rows if "label" in row]
    assert [(row["source_user"], row["label"]) for row in rows] == [
        ("Alex", "produce_resource"),
        ("Alex", "list_resource"),
        ("Blair", "buy_listing"),
    ]
    trade = rows[-1]["data"]
    assert trade["resource"] == "food"
    assert trade["quantity"] == 2
    assert trade["price"] == 5
    assert trade["target_user"] == "Alex"  # the counterparty, for flow analysis
    assert "message" in trade  # human-readable narration is kept alongside


def test_market_failed_actions_leave_no_committed_row(market):
    assert not market.invoke_action_detailed(
        "produce_resource", {"agent_name": "Alex", "resource": "ore", "quantity": 1}
    )[0]
    assert not market.invoke_action_detailed(
        "buy_listing", {"agent_name": "Blair", "listing_id": 404}
    )[0]
    assert not market.invoke_action_detailed(
        "consume_resource", {"agent_name": "Alex", "resource": "food", "quantity": 99}
    )[0]

    assert [row for row in market.action_logger.rows if "label" in row] == []
    assert market.count_committed_events() == 0


def test_market_events_still_reach_the_observation_feed(market):
    market.invoke_action_detailed(
        "produce_resource", {"agent_name": "Alex", "resource": "food", "quantity": 1}
    )
    assert "Alex produced 1 food." in market.observe("Alex")


def test_virtual_space_actions_log_structured_events():
    app = VirtualSpaceApp(rooms=["atrium"], starting_room="atrium")
    app.action_logger = _RecordingLogger()
    app.initialize(agent_names=["Alex", "Blair"])
    app.invoke_action_detailed(
        "talk", {"agent_name": "Alex", "target_user": "Blair", "message": "hello"}
    )

    (row,) = [row for row in app.action_logger.rows if "label" in row]
    assert row["source_user"] == "Alex"
    assert row["label"] == "talk"
    assert row["data"]["target_user"] == "Blair"
    assert row["data"]["text"] == "hello"


def test_market_state_round_trips_through_a_checkpoint(market):
    market.invoke_action_detailed(
        "produce_resource", {"agent_name": "Alex", "resource": "food", "quantity": 2}
    )
    state = market.get_state()

    restored = ResourceMarketApp(production_capabilities={"farmer": {"food": 3}}, role_needs={})
    restored.action_logger = _RecordingLogger()
    restored.initialize(agent_names=["Alex", "Blair"], sim_roles={"Alex": "farmer"})
    restored.set_state(state)
    assert "Alex produced 2 food." in restored.observe("Alex")
    assert list(restored.iter_committed_events()) == list(market.iter_committed_events())


class _AutoLoggingBackend(BackendApp):
    def name(self) -> str:
        return "auto"

    def description(self) -> str:
        return "test backend"

    @app_action(log_as="changed", tags=("custom.change",))
    def change(self, agent_name: str, value: int, optional: str | None = None) -> ActionResult:
        return ActionResult("done", data={"derived": value * 2})

    @app_action
    def reject(self, agent_name: str) -> ActionResult:
        return ActionResult("no", committed=False)

    @app_action(log=False)
    def inspect(self, agent_name: str) -> str:
        return "state"

    @app_action
    def manual(self, agent_name: str) -> str:
        self._log_action_event(agent_name, "manual_label", {"custom": True})
        return "manual"


def test_auto_logging_contract() -> None:
    app = _AutoLoggingBackend()
    app.action_logger = _RecordingLogger()

    assert app.invoke_action_detailed(
        "change", {"agent_name": "Alex", "value": 3, "optional": None}
    ) == (True, "done")
    assert app.invoke_action_detailed("reject", {"agent_name": "Alex"}) == (False, "no")
    assert app.invoke_action_detailed("inspect", {"agent_name": "Alex"}) == (True, "state")
    assert app.invoke_action_detailed("manual", {"agent_name": "Alex"}) == (True, "manual")

    assert app.action_logger.rows == [
        {
            "source_user": "Alex",
            "label": "changed",
            "data": {"value": 3, "derived": 6, "message": "done"},
        },
        {
            "source_user": "Alex",
            "label": "manual_label",
            "data": {"custom": True},
        },
    ]


def test_text_invocation_unwraps_action_result() -> None:
    app = _AutoLoggingBackend()
    app.action_logger = _RecordingLogger()
    descriptor = next(action for action in app.actions() if action.name == "change")

    assert app.invoke_action(descriptor, "agent_name: Alex\nvalue: 4") == "done"
    assert app.action_logger.rows[0]["data"]["derived"] == 8


def test_finished_is_an_attributed_control_event() -> None:
    app = _AutoLoggingBackend()
    app.action_logger = _RecordingLogger()
    resolve = GenericActionResolveComponent(backend=app)

    result = resolve.resolve(
        active_agent="Alex",
        action=ActionOutput.from_text("ACTION: FINISHED"),
    )

    assert result == "Finished action episode"
    assert app.action_logger.rows == [
        {
            "source_user": "Alex",
            "label": "finish_action_episode",
            "data": {"message": "Finished action episode"},
        }
    ]
    descriptor = next(action for action in app.actions() if action.name == "finish_action_episode")
    assert descriptor.tags == ("control",)
    assert descriptor.agent_visible_parameters == []
    assert app.count_committed_events() == 1


def test_checkpoint_replay_ignores_finished_control_events() -> None:
    row = {
        "event_type": "action",
        "label": "finish_action_episode",
        "source_user": "Alex",
        "episode": 1,
        "data": {"message": "Finished action episode"},
    }
    assert _replay_event_fields(row, checkpoint_step=2) is None
