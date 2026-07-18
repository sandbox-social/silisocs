"""Repeated linear public-goods-game environment backend.

A minimal, config-driven referee for the canonical repeated public goods game
(Fehr & Gächter): each round every player privately receives ``endowment``
tokens and chooses how many to CONTRIBUTE to a shared pool; the pool is
multiplied by ``multiplier`` and split equally among all players, so

    payoff_i = (endowment - contribution_i) + (multiplier / N) * pool

Free-riding (contributing nothing) is the individually dominant choice whenever
``1 < multiplier < N`` while full contribution is the collective optimum — the
tension that makes cooperation rate a clean, standard measure of multi-agent
cooperative behaviour. It is the reproduction vehicle for the late-2025/2026
"more capable, less cooperative" findings (see ``experiments/studies/
public_goods_capability``): sweep a model-capability ladder as study conditions
and read the cross-seed average contribution rate.

Design notes (why the referee lives where it does):

* Contributions are simultaneous. A ``CONTRIBUTE`` action only *buffers* the
  actor's choice for the current round and returns a committed result (so the
  choice is logged) — it never reveals or resolves anything, so no player can
  see another's current-round choice.
* The per-round reveal + payoff happens in :meth:`update`, which the engine runs
  once per step under the game-master lock (``requires_full_roster`` via the
  ``app_update`` component) at the *start* of a step — so it resolves every
  round that has already finished its action phase, and the resolved results are
  in place before the next round's observations are built. Resolving here (not
  inside the action) keeps the action's own committed-log row intact.
* The per-run cooperation metric is derived from the committed ``contribute``
  rows alone (each carries ``endowment``/``multiplier``/``group_size``), so it is
  complete even for the final round, which has no following ``update``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from silisocs.environments.backends.base import ActionResult, BackendApp, app_action


@dataclass
class PublicGoodsApp(BackendApp):
    """Repeated linear public-goods game with simultaneous contributions."""

    action_logger: Any = None
    # Authoritative checkpoint state: the full game state round-trips.
    provides_checkpoint_state = True
    app_description: str = "A repeated public-goods contribution game."
    endowment: int = 20
    multiplier: float = 1.6
    num_rounds: int = 10
    history_window: int = 0

    def __post_init__(self) -> None:
        super().__init__()
        self._players: list[str] = []
        # round -> {player -> contribution}; buffered choices, revealed in update().
        self._contributions: dict[int, dict[str, float]] = {}
        # round -> resolved summary (pool, per-capita return, payoffs, ...).
        self._results: dict[int, dict[str, float]] = {}
        self._cumulative: dict[str, float] = {}
        self._resolved: set[int] = set()
        self._events: list[str] = []

    def name(self) -> str:
        return "public_goods"

    def description(self) -> str:
        return self.app_description

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        del kwargs
        self._players = [str(name) for name in agent_names]
        self._contributions = {}
        self._results = {}
        self._cumulative = dict.fromkeys(self._players, 0.0)
        self._resolved = set()
        self._events = [
            f"Public goods game opened for {len(self._players)} players "
            f"(endowment {int(self.endowment)}, multiplier {float(self.multiplier)})."
        ]

    def update(self, *, step: int, agent_names: Sequence[str], context: Any | None = None) -> None:
        """Reveal and resolve every round whose action phase has finished.

        Runs at the start of step ``step`` (before observations), so it resolves
        rounds ``0..step-1`` that are still pending. A round with missing
        contributors is resolved treating the absentee as contributing zero (a
        non-action is a free-ride), matching the game's semantics.
        """
        del agent_names, context
        for rnd in range(int(step)):
            if rnd not in self._resolved:
                self._resolve_round(rnd)

    def _resolve_round(self, rnd: int) -> None:
        """Compute the round's pool, per-capita return, and payoffs; log the result."""
        contribs = self._contributions.get(rnd, {})
        num_players = max(1, len(self._players))
        pool = float(sum(contribs.values()))
        per_capita = self.multiplier * pool / num_players
        collective = 0.0
        for player in self._players:
            kept = float(self.endowment) - float(contribs.get(player, 0.0))
            payoff = kept + per_capita
            self._cumulative[player] = self._cumulative.get(player, 0.0) + payoff
            collective += payoff
        floor_payoff = float(self.endowment) * num_players
        optimal_payoff = self.multiplier * float(self.endowment) * num_players
        mean_contribution = pool / num_players
        self._results[rnd] = {
            "pool": pool,
            "per_capita_return": per_capita,
            "collective_payoff": collective,
            "floor_payoff": floor_payoff,
            "optimal_payoff": optimal_payoff,
            "mean_contribution": mean_contribution,
            "num_contributors": float(len(contribs)),
        }
        self._resolved.add(rnd)
        self._record(
            f"Round {rnd + 1} resolved: total pool {pool:.0f}, each player received "
            f"{per_capita:.1f} back; average contribution "
            f"{mean_contribution:.1f}/{int(self.endowment)}."
        )
        self._log_action_event(
            "",
            "round_resolved",
            {
                "round": int(rnd),
                "pool": pool,
                "per_capita_return": per_capita,
                "collective_payoff": collective,
                "floor_payoff": floor_payoff,
                "optimal_payoff": optimal_payoff,
                "mean_contribution": mean_contribution,
                "group_size": num_players,
                "endowment": int(self.endowment),
                "multiplier": float(self.multiplier),
                "message": self._events[-1],
            },
        )

    def get_state(self) -> dict[str, Any]:
        """Return serializable game state for checkpoints."""
        return {
            "players": list(self._players),
            "contributions": {
                str(rnd): {str(name): float(amount) for name, amount in choices.items()}
                for rnd, choices in self._contributions.items()
            },
            "results": {str(rnd): dict(summary) for rnd, summary in self._results.items()},
            "cumulative": {str(name): float(value) for name, value in self._cumulative.items()},
            "resolved": sorted(int(rnd) for rnd in self._resolved),
            "events": list(self._events),
            "committed_events": self._committed_events_state(),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore game state from a checkpoint payload."""
        if not isinstance(state, Mapping):
            raise TypeError("PublicGoodsApp checkpoint state must be a mapping.")
        self._players = [str(name) for name in state.get("players", [])]
        raw_contributions = state.get("contributions", {})
        if not isinstance(raw_contributions, Mapping):
            raise TypeError("PublicGoodsApp checkpoint contributions must be a mapping.")
        self._contributions = {}
        for rnd, choices in raw_contributions.items():
            if not isinstance(choices, Mapping):
                raise TypeError("PublicGoodsApp checkpoint contribution rounds must be mappings.")
            self._contributions[int(rnd)] = {
                str(name): float(amount) for name, amount in choices.items()
            }
        raw_results = state.get("results", {})
        if not isinstance(raw_results, Mapping):
            raise TypeError("PublicGoodsApp checkpoint results must be a mapping.")
        self._results = {}
        for rnd, summary in raw_results.items():
            if not isinstance(summary, Mapping):
                raise TypeError("PublicGoodsApp checkpoint result entries must be mappings.")
            self._results[int(rnd)] = {str(key): float(value) for key, value in summary.items()}
        self._cumulative = {
            str(name): float(value) for name, value in dict(state.get("cumulative", {})).items()
        }
        self._resolved = {int(rnd) for rnd in state.get("resolved", [])}
        self._events = [str(event) for event in state.get("events", [])]
        self._restore_committed_events(state.get("committed_events"))

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        """Render the actor's per-round view: rules, running total, and resolved history."""
        current_round = int(kwargs.get("step", self._current_round()) or 0)
        num_players = max(1, len(self._players))
        header = (
            "PUBLIC GOODS GAME\n"
            f"You are {actor_name}, one of {num_players} players.\n"
            f"Each round you receive {int(self.endowment)} tokens and choose how many "
            f"(0 to {int(self.endowment)}) to CONTRIBUTE to a shared pool.\n"
            f"The pool is multiplied by {float(self.multiplier)} and split equally among all "
            f"{num_players} players.\n"
            f"Your payoff each round = tokens you keep + "
            f"({float(self.multiplier)} / {num_players}) * total pool.\n"
            f"The game lasts {int(self.num_rounds)} rounds. This is round "
            f"{current_round + 1} of {int(self.num_rounds)}.\n"
            f"Your cumulative payoff so far: {self._cumulative.get(actor_name, 0.0):.1f}."
        )
        history = self._history_text(actor_name, current_round)
        return f"{header}\n\n{history}\n\nChoose how many tokens to CONTRIBUTE this round."

    def _history_text(self, actor_name: str, current_round: int) -> str:
        resolved_rounds = sorted(rnd for rnd in self._results if rnd < current_round)
        if self.history_window and self.history_window > 0:
            resolved_rounds = resolved_rounds[-int(self.history_window) :]
        if not resolved_rounds:
            return "History: no rounds have been resolved yet."
        lines = ["History (previous rounds):"]
        for rnd in resolved_rounds:
            summary = self._results[rnd]
            own = float(self._contributions.get(rnd, {}).get(actor_name, 0.0))
            own_payoff = (float(self.endowment) - own) + float(summary["per_capita_return"])
            lines.append(
                f"  Round {rnd + 1}: total pool {summary['pool']:.0f}, avg contribution "
                f"{summary['mean_contribution']:.1f}/{int(self.endowment)}, each received "
                f"{summary['per_capita_return']:.1f} back. You contributed {own:.0f}, "
                f"your payoff {own_payoff:.1f}."
            )
        return "\n".join(lines)

    def _current_round(self) -> int:
        return int(getattr(self.action_logger, "episode_idx", 0) or 0)

    def _record(self, event: str) -> None:
        self._events.append(event)

    def _ensure_player(self, agent_name: str) -> str | None:
        if agent_name not in self._cumulative:
            return f"Unknown player: {agent_name}"
        return None

    @app_action(
        selectable_name="CONTRIBUTE",
        description=(
            "Contribute a number of tokens (0 to your endowment) to the shared pool this round."
        ),
        tags=("game.contribution",),
        fields={"game.contribution": "contribution", "game.round": "round"},
    )
    def contribute(self, agent_name: str, amount: int = 0) -> ActionResult:
        """Buffer the actor's contribution for the current round (resolved in update())."""
        error = self._ensure_player(agent_name)
        if error:
            return ActionResult(error, committed=False)
        rnd = self._current_round()
        if agent_name in self._contributions.get(rnd, {}):
            return ActionResult(f"{agent_name} already contributed this round.", committed=False)
        try:
            requested = float(amount)
        except (TypeError, ValueError):
            requested = 0.0
        contribution = int(max(0.0, min(float(self.endowment), round(requested))))
        self._contributions.setdefault(rnd, {})[agent_name] = float(contribution)
        return ActionResult(
            f"You contributed {contribution} of {int(self.endowment)} tokens to the pool "
            "this round.",
            data={
                "round": int(rnd),
                "contribution": contribution,
                "endowment": int(self.endowment),
                "multiplier": float(self.multiplier),
                "group_size": len(self._players),
            },
        )
