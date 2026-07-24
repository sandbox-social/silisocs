from __future__ import annotations

from typing import Any

from silisocs.environments.backends.public_goods.app import PublicGoodsApp


class _Logger:
    """Minimal action logger with a controllable episode index."""

    def __init__(self) -> None:
        self.episode_idx = 0
        self.rows: list[dict] = []

    def log(self, row: dict) -> None:
        self.rows.append(row)


def _app(**kwargs) -> PublicGoodsApp:
    app = PublicGoodsApp(**kwargs)
    app.initialize(agent_names=["Alex", "Blair", "Casey", "Devon"])
    app.action_logger = _Logger()
    return app


def _contribute(app: PublicGoodsApp, name: str, amount: Any) -> str:
    return app.invoke_action_with_kwargs("CONTRIBUTE", {"agent_name": name, "amount": amount})


def test_public_goods_observe_explains_the_game() -> None:
    app = _app(endowment=20, multiplier=1.6, num_rounds=10)
    observation = app.observe("Alex", step=0)
    assert "PUBLIC GOODS GAME" in observation
    assert "one of 4 players" in observation
    assert "20 tokens" in observation
    assert "round 1 of 10" in observation
    assert "no rounds have been resolved yet" in observation


def test_public_goods_contribute_buffers_valid_amounts() -> None:
    app = _app(endowment=20)
    _contribute(app, "Alex", 15)
    _contribute(app, "Blair", 0)
    _contribute(app, "Casey", 20)
    assert app._contributions[0] == {"Alex": 15.0, "Blair": 0.0, "Casey": 20.0}


def test_public_goods_rejects_out_of_range_or_malformed_amounts() -> None:
    """A formatting glitch must not be recorded as a deliberate choice.

    Clamping (999 -> 20, -5 -> 0) would make a model's tool-call error look like
    genuine cooperation or defection in the metric; rejection gives the agent an
    error to retry on and keeps the committed log honest.
    """
    app = _app(endowment=20)
    for bad in (999, -5, "lots", float("nan")):
        message = _contribute(app, "Alex", bad)
        assert "between 0 and 20" in message
    assert app._contributions == {}  # nothing buffered, nothing committed
    assert app.count_committed_events(labels=["contribute"]) == 0
    # A valid retry still lands: rejection must not burn the round.
    _contribute(app, "Alex", 10)
    assert app._contributions[0] == {"Alex": 10.0}


def test_public_goods_rejects_double_contribution_in_a_round() -> None:
    app = _app()
    _contribute(app, "Alex", 5)
    message = _contribute(app, "Alex", 10)
    assert "already contributed" in message
    assert app._contributions[0]["Alex"] == 5.0  # unchanged


def test_public_goods_rejects_unknown_player() -> None:
    app = _app()
    message = _contribute(app, "Stranger", 5)
    assert "Unknown player" in message
    assert app._contributions == {}


def test_public_goods_update_resolves_round_and_pays_out() -> None:
    app = _app(endowment=20, multiplier=1.6)
    _contribute(app, "Alex", 20)
    _contribute(app, "Blair", 0)
    _contribute(app, "Casey", 10)
    _contribute(app, "Devon", 10)
    # update at the start of the next step resolves the finished round.
    app.action_logger.episode_idx = 1
    app.update(step=1, agent_names=["Alex", "Blair", "Casey", "Devon"])

    result = app._results[0]
    assert result["pool"] == 40.0
    assert result["per_capita_return"] == 16.0  # 1.6 * 40 / 4
    assert result["collective_payoff"] == 104.0  # N*E + (r-1)*pool = 80 + 0.6*40
    assert result["optimal_payoff"] == 128.0  # r*N*E
    # payoff_i = (E - c_i) + per_capita
    assert app._cumulative == {"Alex": 16.0, "Blair": 36.0, "Casey": 26.0, "Devon": 26.0}


def test_public_goods_observation_shows_resolved_history() -> None:
    app = _app(endowment=20, multiplier=1.6)
    _contribute(app, "Alex", 20)
    _contribute(app, "Blair", 0)
    _contribute(app, "Casey", 10)
    _contribute(app, "Devon", 10)
    app.action_logger.episode_idx = 1
    app.update(step=1, agent_names=["Alex", "Blair", "Casey", "Devon"])

    observation = app.observe("Blair", step=1)
    assert "History (previous rounds):" in observation
    assert "Round 1: total pool 40" in observation
    assert "You contributed 0, your payoff 36.0" in observation


def test_public_goods_observation_does_not_leak_current_round_choices() -> None:
    app = _app()
    # Alex contributes first this round; Blair (a later actor) must not see it.
    _contribute(app, "Alex", 20)
    observation = app.observe("Blair", step=0)
    assert "Alex" not in observation.split("Choose")[0].replace("You are Blair", "")
    assert "no rounds have been resolved yet" in observation


def test_public_goods_finished_action_is_available() -> None:
    app = _app()
    assert app.invoke_action_with_kwargs("FINISHED", {}) == "Finished action episode"


def test_public_goods_contribute_logs_game_parameters() -> None:
    app = _app(endowment=20, multiplier=1.6)
    _contribute(app, "Alex", 12)
    events = list(app.iter_committed_events(labels=["contribute"]))
    assert len(events) == 1
    data = events[0]["data"]
    assert data["contribution"] == 12
    assert data["endowment"] == 20
    assert data["multiplier"] == 1.6
    assert data["group_size"] == 4
    assert data["round"] == 0


def test_public_goods_checkpoint_restores_game_state() -> None:
    app = _app(endowment=20, multiplier=1.6)
    _contribute(app, "Alex", 20)
    _contribute(app, "Blair", 0)
    _contribute(app, "Casey", 10)
    _contribute(app, "Devon", 10)
    app.action_logger.episode_idx = 1
    app.update(step=1, agent_names=["Alex", "Blair", "Casey", "Devon"])
    _contribute(app, "Alex", 5)  # a buffered, unresolved round-1 choice

    restored = PublicGoodsApp(endowment=20, multiplier=1.6)
    restored.action_logger = _Logger()
    restored.set_state(app.get_state())

    assert restored._cumulative == app._cumulative
    assert restored._results[0]["pool"] == 40.0
    assert restored._contributions[1] == {"Alex": 5.0}  # in-flight choice survives


def test_public_goods_checkpoint_round_trips_committed_events_mirror() -> None:
    app = _app(endowment=20, multiplier=1.6)
    _contribute(app, "Alex", 20)
    _contribute(app, "Blair", 5)
    app.action_logger.episode_idx = 1
    app.update(step=1, agent_names=["Alex", "Blair", "Casey", "Devon"])
    expected = app.count_committed_events()
    assert expected > 0

    restored = PublicGoodsApp(endowment=20, multiplier=1.6)
    restored.action_logger = _Logger()
    restored.set_state(app.get_state())
    assert restored.count_committed_events() == expected
    assert list(restored.iter_committed_events()) == list(app.iter_committed_events())
