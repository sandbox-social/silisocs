"""Reusable referee base for simultaneous-move repeated games.

``SimultaneousRoundGame`` owns the machinery every simultaneous-reveal game
(public goods, prisoner's dilemma, matching games, auctions, ...) otherwise
re-derives from scratch:

* **Hidden per-round choice buffering** — an action records exactly one choice
  per player per round and never reveals anything; repeat submissions are
  rejected so no player can revise after seeing others act.
* **Resolve-at-the-round-boundary** — :meth:`update` (the generic ``app_update``
  component) reveals and resolves every round whose action phase has finished,
  strictly before the next round's observations are built.
* **Payoff bookkeeping** — per-player cumulative payoffs and per-round result
  summaries, both available to observation text.
* **Committed-event logging** — each resolved round appends a ``round_resolved``
  row carrying the summary, so evaluators can score runs from the action log
  alone (see ``experiments/studies/public_goods_capability/eval.py``).
* **Checkpoint round-trip** — the full game state, including a round that is
  mid-buffer (some players chose, the round is unresolved), restores exactly.

A concrete game implements the payoff rule and the player-facing prose:

* :meth:`resolve_round` — the referee's reveal: given a round's buffered
  choices, return a :class:`RoundResult` (summary, per-player payoffs,
  narrative, extra logged fields). Absent players are the hook's to interpret
  (public goods treats a non-action as contributing zero).
* :meth:`observe` — the per-round view (rules, history, running totals);
  fully game-specific, so it stays abstract at this level.
* an ``@app_action`` per legal move that validates its inputs and buffers via
  :meth:`record_choice`.

``PublicGoodsApp`` is the reference subclass. Choice values are opaque to this
base (floats, strings, dicts, ...) but must be JSON-serializable so checkpoints
round-trip.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from silisocs.environments.backends.base import BackendApp


@dataclass
class RoundResult:
    """What a game's :meth:`~SimultaneousRoundGame.resolve_round` reveals.

    ``summary`` is stored as the round's result (readable by observation text)
    and logged on the ``round_resolved`` event; ``payoffs`` are added to each
    player's cumulative total; ``narrative`` is the human-readable line recorded
    to the event feed and logged as the event's ``message``; ``event_extra``
    holds parameters that belong on the logged row but not in the stored summary
    (e.g. the game's constants, so each row is self-describing for evaluators).
    """

    summary: dict[str, float]
    payoffs: dict[str, float]
    narrative: str
    event_extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimultaneousRoundGame(BackendApp):
    """Base referee for repeated games with simultaneous hidden choices."""

    action_logger: Any = None
    # Round-game state is small and self-contained: subclasses snapshot it.
    provides_checkpoint_state = True
    # Verb used in the repeat-submission rejection ("Alex already {verb} this
    # round."); override so the agent-facing message speaks the game's language.
    choice_verb = "chose"

    def __post_init__(self) -> None:
        super().__init__()
        self._players: list[str] = []
        # round -> {player -> choice}; buffered privately, revealed in update().
        self._choices: dict[int, dict[str, Any]] = {}
        # round -> resolved summary as returned by resolve_round().
        self._results: dict[int, dict[str, float]] = {}
        self._cumulative: dict[str, float] = {}
        self._resolved: set[int] = set()
        self._events: list[str] = []

    # ---- game hooks -----------------------------------------------------

    @abc.abstractmethod
    def resolve_round(self, rnd: int, choices: Mapping[str, Any]) -> RoundResult:
        """Reveal a finished round: compute its summary, payoffs, and narrative."""
        raise NotImplementedError

    def opening_message(self) -> str:
        """The event recorded when the game starts (override for game flavor)."""
        return f"{self.name()} opened for {len(self._players)} players."

    # ---- engine-facing lifecycle ---------------------------------------

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        del kwargs
        self._players = [str(name) for name in agent_names]
        self._choices = {}
        self._results = {}
        self._cumulative = dict.fromkeys(self._players, 0.0)
        self._resolved = set()
        self._events = [self.opening_message()]

    def update(self, *, step: int, agent_names: Sequence[str], context: Any | None = None) -> None:
        """Reveal and resolve every round whose action phase has finished.

        Runs at the start of step ``step`` (before observations), so it resolves
        rounds ``0..step-1`` that are still pending. What a missing choice means
        is the game's call, made inside :meth:`resolve_round` (public goods
        treats an absentee as contributing zero — a non-action is a free-ride).
        """
        del agent_names, context
        for rnd in range(int(step)):
            if rnd not in self._resolved:
                self._resolve_round(rnd)

    def _resolve_round(self, rnd: int) -> None:
        """Run the game's reveal for one round and record every trace of it."""
        result = self.resolve_round(rnd, dict(self._choices.get(rnd, {})))
        for player, payoff in result.payoffs.items():
            self._cumulative[player] = self._cumulative.get(player, 0.0) + float(payoff)
        self._results[rnd] = dict(result.summary)
        self._resolved.add(rnd)
        self._record(result.narrative)
        self._log_action_event(
            "",
            "round_resolved",
            {
                "round": int(rnd),
                **result.summary,
                **result.event_extra,
                "message": result.narrative,
            },
        )

    # ---- choice buffering ----------------------------------------------

    def record_choice(self, agent_name: str, value: Any) -> str | None:
        """Buffer one hidden choice for the current round.

        Returns an error message (for an unknown player or a repeat submission)
        or ``None`` when the choice was recorded. Input validation — what values
        are legal moves — happens in the game's action before calling this.
        """
        error = self._ensure_player(agent_name)
        if error:
            return error
        rnd = self.current_episode()
        if agent_name in self._choices.get(rnd, {}):
            return f"{agent_name} already {self.choice_verb} this round."
        self._choices.setdefault(rnd, {})[agent_name] = value
        return None

    def _ensure_player(self, agent_name: str) -> str | None:
        if agent_name not in self._cumulative:
            return f"Unknown player: {agent_name}"
        return None

    def _record(self, event: str) -> None:
        self._events.append(event)

    def resolved_rounds_before(self, rnd: int, window: int = 0) -> list[int]:
        """Resolved round indices before ``rnd``, optionally last-``window`` only."""
        rounds = sorted(r for r in self._results if r < rnd)
        if window and window > 0:
            rounds = rounds[-int(window) :]
        return rounds

    # ---- checkpoint contract -------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Return serializable game state for checkpoints."""
        return {
            "players": list(self._players),
            "choices": {str(rnd): dict(choices.items()) for rnd, choices in self._choices.items()},
            "results": {str(rnd): dict(summary) for rnd, summary in self._results.items()},
            "cumulative": {str(name): float(value) for name, value in self._cumulative.items()},
            "resolved": sorted(int(rnd) for rnd in self._resolved),
            "events": list(self._events),
            "committed_events": self._committed_events_state(),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore game state from a checkpoint payload."""
        label = type(self).__name__
        if not isinstance(state, Mapping):
            raise TypeError(f"{label} checkpoint state must be a mapping.")
        self._players = [str(name) for name in state.get("players", [])]
        raw_choices = state.get("choices", {})
        if not isinstance(raw_choices, Mapping):
            raise TypeError(f"{label} checkpoint choices must be a mapping.")
        self._choices = {}
        for rnd, choices in raw_choices.items():
            if not isinstance(choices, Mapping):
                raise TypeError(f"{label} checkpoint choice rounds must be mappings.")
            self._choices[int(rnd)] = {str(name): value for name, value in choices.items()}
        raw_results = state.get("results", {})
        if not isinstance(raw_results, Mapping):
            raise TypeError(f"{label} checkpoint results must be a mapping.")
        self._results = {}
        for rnd, summary in raw_results.items():
            if not isinstance(summary, Mapping):
                raise TypeError(f"{label} checkpoint result entries must be mappings.")
            self._results[int(rnd)] = {str(key): float(value) for key, value in summary.items()}
        self._cumulative = {
            str(name): float(value) for name, value in dict(state.get("cumulative", {})).items()
        }
        self._resolved = {int(rnd) for rnd in state.get("resolved", [])}
        self._events = [str(event) for event in state.get("events", [])]
        self._restore_committed_events(state.get("committed_events"))
