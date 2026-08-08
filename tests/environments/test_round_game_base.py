"""Contract tests for the SimultaneousRoundGame referee base.

A toy matching game (players pick "A" or "B"; majority side scores 1 each)
exercises the base directly, so a second real game (repeated PD, trust game,
...) inherits referee mechanics that are already proven here: hidden buffering,
repeat rejection, resolve-at-the-boundary, cumulative payoffs, round_resolved
logging, and the checkpoint round-trip including an in-flight round.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from silisocs.environments.backends.base import ActionResult, app_action
from silisocs.environments.backends.round_game import RoundResult, SimultaneousRoundGame


@dataclass
class MatchingGame(SimultaneousRoundGame):
    """Pick a side; everyone on the majority side scores one point."""

    app_description: str = "A majority matching game."
    choice_verb = "picked"

    def name(self) -> str:
        return "matching_game"

    def description(self) -> str:
        return self.app_description

    def resolve_round(self, rnd: int, choices: Mapping[str, Any]) -> RoundResult:
        votes = {"A": 0, "B": 0}
        for side in choices.values():
            votes[str(side)] += 1
        majority = "A" if votes["A"] >= votes["B"] else "B"
        payoffs = {
            player: 1.0 if choices.get(player) == majority else 0.0 for player in self._players
        }
        return RoundResult(
            summary={"votes_a": float(votes["A"]), "votes_b": float(votes["B"])},
            payoffs=payoffs,
            narrative=f"Round {rnd + 1}: majority picked {majority}.",
            event_extra={"majority": majority},
        )

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        del kwargs
        return f"MATCHING GAME. You are {actor_name}. Pick A or B."

    @app_action(selectable_name="PICK", description="Pick side A or B this round.")
    def pick(self, agent_name: str, side: str = "A") -> ActionResult:
        if side not in ("A", "B"):
            return ActionResult(f"Side must be A or B; got {side!r}.", committed=False)
        error = self.record_choice(agent_name, side)
        if error:
            return ActionResult(error, committed=False)
        return ActionResult(f"You picked {side}.", data={"side": side})


class _Logger:
    def __init__(self) -> None:
        self.episode_idx = 0
        self.rows: list[dict] = []

    def log(self, row: dict) -> None:
        self.rows.append(row)


def _game() -> MatchingGame:
    game = MatchingGame()
    game.initialize(agent_names=["Alex", "Blair", "Casey"])
    game.action_logger = _Logger()
    return game


def _pick(game: MatchingGame, name: str, side: str) -> str:
    return game.invoke_action_with_kwargs("PICK", {"agent_name": name, "side": side})


def test_choices_buffer_hidden_and_repeats_use_the_game_verb() -> None:
    game = _game()
    _pick(game, "Alex", "A")
    _pick(game, "Blair", "B")
    assert game._choices[0] == {"Alex": "A", "Blair": "B"}
    assert game._results == {}  # nothing revealed until update()
    message = _pick(game, "Alex", "B")
    assert "already picked this round" in message
    assert game._choices[0]["Alex"] == "A"  # first choice stands
    assert "Unknown player" in _pick(game, "Zed", "A")


def test_update_resolves_finished_rounds_with_payoffs_and_logged_rows() -> None:
    game = _game()
    _pick(game, "Alex", "A")
    _pick(game, "Blair", "A")
    _pick(game, "Casey", "B")
    game.action_logger.episode_idx = 1
    game.update(step=1, agent_names=[])
    assert game._results[0] == {"votes_a": 2.0, "votes_b": 1.0}
    assert game._cumulative == {"Alex": 1.0, "Blair": 1.0, "Casey": 0.0}
    resolved = [row for row in game.action_logger.rows if row["label"] == "round_resolved"]
    assert len(resolved) == 1
    data = resolved[0]["data"]
    # Summary, extra fields, and the narrative all ride on the logged row.
    assert data["round"] == 0
    assert data["votes_a"] == 2.0
    assert data["majority"] == "A"
    assert "majority picked A" in data["message"]
    # Resolution is idempotent: a repeated update never re-resolves a round.
    game.update(step=1, agent_names=[])
    assert game._cumulative["Alex"] == 1.0


def test_absent_player_is_the_hooks_call_and_round_still_resolves() -> None:
    game = _game()
    _pick(game, "Alex", "B")  # Blair and Casey never act
    game.update(step=1, agent_names=[])
    assert game._results[0] == {"votes_a": 0.0, "votes_b": 1.0}
    assert game._cumulative == {"Alex": 1.0, "Blair": 0.0, "Casey": 0.0}


def test_checkpoint_round_trips_including_an_in_flight_round() -> None:
    game = _game()
    _pick(game, "Alex", "A")
    _pick(game, "Blair", "A")
    game.update(step=1, agent_names=[])
    game.action_logger.episode_idx = 1
    _pick(game, "Casey", "B")  # round 1 is mid-buffer, unresolved

    payload = json.loads(json.dumps(game.get_state()))  # a real checkpoint is JSON
    restored = MatchingGame()
    restored.action_logger = _Logger()
    restored.set_state(payload)

    assert restored._players == ["Alex", "Blair", "Casey"]
    assert restored._choices[1] == {"Casey": "B"}  # in-flight choice survives
    assert restored._results[0] == {"votes_a": 2.0, "votes_b": 0.0}
    assert restored._cumulative == {"Alex": 1.0, "Blair": 1.0, "Casey": 0.0}
    assert restored._resolved == {0}
    # The committed-events mirror restores with the snapshot.
    assert restored.count_committed_events(labels=["pick"]) == game.count_committed_events(
        labels=["pick"]
    )


def test_payoffs_for_non_players_fail_loudly() -> None:
    """A buggy resolve_round paying a non-player must not corrupt cumulative state."""

    @dataclass
    class _LeakyGame(MatchingGame):
        def resolve_round(self, rnd: int, choices: Mapping[str, Any]) -> RoundResult:
            result = super().resolve_round(rnd, choices)
            result.payoffs["Intruder"] = 99.0
            return result

    game = _LeakyGame()
    game.initialize(agent_names=["Alex", "Blair"])
    game.action_logger = _Logger()
    _pick(game, "Alex", "A")
    try:
        game.update(step=1, agent_names=[])
        raise AssertionError("expected ValueError for a non-player payoff")
    except ValueError as error:
        assert "Intruder" in str(error)
    assert "Intruder" not in game._cumulative  # nothing leaked into totals


def test_initialize_resets_a_played_game() -> None:
    game = _game()
    _pick(game, "Alex", "A")
    game.update(step=1, agent_names=[])
    game.initialize(agent_names=["Dana", "Eli"])
    assert game._players == ["Dana", "Eli"]
    assert game._choices == {}
    assert game._results == {}
    assert game._cumulative == {"Dana": 0.0, "Eli": 0.0}
    assert game._events == [game.opening_message()]
