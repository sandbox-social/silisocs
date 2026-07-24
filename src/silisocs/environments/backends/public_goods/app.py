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

The referee mechanics — simultaneous hidden choice buffering, reveal-at-the-
round-boundary in ``update()``, cumulative payoffs, ``round_resolved`` logging,
and the checkpoint round-trip — live in the shared
:class:`~silisocs.environments.backends.round_game.SimultaneousRoundGame` base
(this game is its reference subclass); this module holds only what makes the
game *public goods*: the CONTRIBUTE action, the payoff rule, and the
player-facing prose.

* The per-run cooperation metric is derived from the committed ``contribute``
  rows alone (each carries ``endowment``/``multiplier``/``group_size``), so it
  is complete even for the final round, which has no following ``update``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from silisocs.environments.backends.base import ActionResult, app_action
from silisocs.environments.backends.round_game import RoundResult, SimultaneousRoundGame


@dataclass
class PublicGoodsApp(SimultaneousRoundGame):
    """Repeated linear public-goods game with simultaneous contributions."""

    app_description: str = "A repeated public-goods contribution game."
    choice_verb = "contributed"
    endowment: int = 20
    multiplier: float = 1.6
    num_rounds: int = 10
    history_window: int = 0

    def name(self) -> str:
        return "public_goods"

    def description(self) -> str:
        return self.app_description

    def opening_message(self) -> str:
        return (
            f"Public goods game opened for {len(self._players)} players "
            f"(endowment {int(self.endowment)}, multiplier {float(self.multiplier)})."
        )

    def resolve_round(self, rnd: int, choices: Mapping[str, Any]) -> RoundResult:
        """The reveal: pool the contributions, multiply, split equally.

        A player with no buffered choice contributed zero — a non-action is a
        free-ride, matching the game's semantics.
        """
        num_players = max(1, len(self._players))
        pool = float(sum(float(amount) for amount in choices.values()))
        per_capita = self.multiplier * pool / num_players
        payoffs: dict[str, float] = {}
        for player in self._players:
            kept = float(self.endowment) - float(choices.get(player, 0.0))
            payoffs[player] = kept + per_capita
        collective = float(sum(payoffs.values()))
        mean_contribution = pool / num_players
        narrative = (
            f"Round {rnd + 1} resolved: total pool {pool:.0f}, each player received "
            f"{per_capita:.1f} back; average contribution "
            f"{mean_contribution:.1f}/{int(self.endowment)}."
        )
        return RoundResult(
            summary={
                "pool": pool,
                "per_capita_return": per_capita,
                "collective_payoff": collective,
                "floor_payoff": float(self.endowment) * num_players,
                "optimal_payoff": self.multiplier * float(self.endowment) * num_players,
                "mean_contribution": mean_contribution,
                "num_contributors": float(len(choices)),
            },
            payoffs=payoffs,
            narrative=narrative,
            event_extra={
                "group_size": num_players,
                "endowment": int(self.endowment),
                "multiplier": float(self.multiplier),
            },
        )

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        """Render the actor's per-round view: rules, running total, and resolved history."""
        current_round = int(kwargs.get("step", self.current_episode()) or 0)
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
        resolved_rounds = self.resolved_rounds_before(current_round, self.history_window)
        if not resolved_rounds:
            return "History: no rounds have been resolved yet."
        lines = ["History (previous rounds):"]
        for rnd in resolved_rounds:
            summary = self._results[rnd]
            own = float(self._choices.get(rnd, {}).get(actor_name, 0.0))
            own_payoff = (float(self.endowment) - own) + float(summary["per_capita_return"])
            lines.append(
                f"  Round {rnd + 1}: total pool {summary['pool']:.0f}, avg contribution "
                f"{summary['mean_contribution']:.1f}/{int(self.endowment)}, each received "
                f"{summary['per_capita_return']:.1f} back. You contributed {own:.0f}, "
                f"your payoff {own_payoff:.1f}."
            )
        return "\n".join(lines)

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
        # Reject rather than clamp: a malformed amount is a formatting error the
        # agent can retry, and silently coercing it to a valid contribution would
        # be indistinguishable from a deliberate choice in the cooperation metric.
        try:
            requested = float(amount)
        except (TypeError, ValueError):
            requested = float("nan")
        if not 0.0 <= requested <= float(self.endowment):
            return ActionResult(
                f"Contribution must be a number of tokens between 0 and "
                f"{int(self.endowment)}; got {amount!r}.",
                committed=False,
            )
        contribution = int(round(requested))
        error = self.record_choice(agent_name, float(contribution))
        if error:
            return ActionResult(error, committed=False)
        return ActionResult(
            f"You contributed {contribution} of {int(self.endowment)} tokens to the pool "
            "this round.",
            data={
                "round": int(self.current_episode()),
                "contribution": contribution,
                "endowment": int(self.endowment),
                "multiplier": float(self.multiplier),
                "group_size": len(self._players),
            },
        )
