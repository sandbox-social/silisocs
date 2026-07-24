#!/usr/bin/env python3
"""Public-goods cooperation/payoff evaluator (deterministic; no LLM calls).

Reads the committed ``contribute`` action events from a run and emits per-run
cooperation scalars into ``aggregated`` — the channel the study runner averages
across replicate seeds (with n/mean/stdev/CI95) into
``generated/organized/summary.json``. Every ``contribute`` row carries the game
parameters (``endowment``/``multiplier``/``group_size``), so the metric is
self-describing and needs no scenario config.

The backend scores a round over the FULL roster, treating a player who did not
``CONTRIBUTE`` that round as contributing 0 (a non-action is a free-ride). This
evaluator matches that: it materializes the full ``group_size x rounds`` grid and
counts every missing ``(player, round)`` cell as 0 — so a capable model that opts
out (emits ``FINISHED``/an off-format action) is scored as free-riding, not
silently excluded. Contributions are deduplicated by ``(player, round)``
(last-write-wins) so the metric is idempotent under a mid-step crash + resume
(which re-appends the interrupted step's rows).

The round count comes from the episodes the engine actually ran
(``sim_metrics.json``'s per-episode records), because the final round has no
following ``update`` — a fully silent last round must count as defection, not
vanish. The manifest's ``num_steps`` is the *configured target*, used only as a
fallback when the metrics file is absent: an interactively stopped or crashed
run played fewer rounds than configured, and scoring the un-run remainder as
silence would deflate cooperation in proportion to how early it stopped. A run
with ZERO contributions scores 0.0 only when its manifest shows a healthy run
(every health counter zero) and the game demonstrably ran (``round_resolved``
rows exist); otherwise the silence is a broken pipeline, not a choice, and the
run is excluded (``None``, dropped from the cross-seed stats) rather than scored
as full defection.

Metrics (all in ``aggregated``):

* ``avg_contribution_rate`` — total contributed / (endowment x roster x rounds).
  The headline cooperation measure (0 = full free-riding, 1 = full cooperation).
* ``efficiency`` — collective payoff normalized between the all-defect floor and
  the all-cooperate ceiling; for a linear public-goods game this equals
  ``avg_contribution_rate`` exactly (a built-in consistency check).
* ``collective_payoff`` — total payoff summed over players and rounds.
* ``pct_of_optimal`` — ``collective_payoff`` as a fraction of the full-cooperation
  ceiling.
* ``free_rider_share`` — fraction of the roster whose contribution rate < 10%
  (players who never acted count as full free-riders).

Usage (the study-runner contract):
    python eval.py --run-dir <run_dir> --output <eval.json>
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from silisocs.evaluations.action_events import resolve_action_event_files
from silisocs.evaluations.run_artifact import iter_jsonl

CONTRIBUTE_LABEL = "contribute"
RESOLVED_LABEL = "round_resolved"
FREE_RIDER_THRESHOLD = 0.10


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _manifest(run_dir: Path) -> dict[str, Any]:
    """The run's self-describing ``run_manifest.json``, or ``{}`` when absent."""
    try:
        data = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _episodes_run(run_dir: Path) -> int | None:
    """Episodes the engine actually executed, from ``sim_metrics.json``.

    Every executed episode appends one ``episode_metrics`` row, so this is the
    ground truth for rounds played even when a run was stopped early or crashed
    mid-study. ``None`` when the metrics file is absent (legacy/synthetic runs).
    """
    try:
        data = json.loads((run_dir / "sim_metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    episodes = data.get("episode_metrics") if isinstance(data, dict) else None
    if not isinstance(episodes, list) or not episodes:
        return None
    indices = [int(_num(row.get("episode"), -1.0)) for row in episodes if isinstance(row, dict)]
    return (max(indices) + 1) if indices and max(indices) >= 0 else None


def _empty_payload(run_dir: Path) -> dict[str, Any]:
    """Payload for a run with no committed contributions (e.g. a disabled-LLM smoke run)."""
    return {
        "source": str(run_dir),
        "aggregated": {
            "avg_contribution_rate": None,
            "efficiency": None,
            "collective_payoff": None,
            "pct_of_optimal": None,
            "free_rider_share": None,
        },
        "agents": {},
        "summary": {"roster": 0, "rounds": 0, "contribution_events": 0},
    }


def evaluate_run_dir(run_dir: Path) -> dict[str, Any]:
    """Compute cooperation/payoff scalars from a run's committed contribution events.

    Reconciled with the backend's payoff semantics: metrics are computed over the
    full ``roster x rounds`` grid (missing decisions = 0), not only over emitted
    rows, so opting out counts as free-riding.
    """
    files = resolve_action_event_files(run_dir)
    rows = [row for row in iter_jsonl(files) if row.get("event_type") == "action"]
    contributions = [row for row in rows if row.get("label") == CONTRIBUTE_LABEL]
    resolved = [row for row in rows if row.get("label") == RESOLVED_LABEL]
    manifest = _manifest(run_dir)

    if not contributions:
        # Total silence is deliberate defection (0.0) only for a demonstrably
        # healthy run; a degraded/unknown pipeline is excluded, not scored.
        health = manifest.get("health")
        failures = (
            sum(value for value in health.values() if isinstance(value, int))
            if isinstance(health, dict)
            else None
        )
        if not resolved or failures != 0:
            return _empty_payload(run_dir)

    # Deduplicate by (player, round), last-write-wins: idempotent under resume.
    # Both row kinds carry the game parameters, so an all-silent run still
    # self-describes through its round_resolved rows. The max() harvest assumes
    # the parameters are constant for the whole run (true for this study; a
    # mid-run intervention changing them would need a per-round harvest).
    cell: dict[tuple[str, int], float] = {}
    endowment = 0.0
    multiplier = 0.0
    roster = 0
    rounds_seen: set[int] = set()
    for row in contributions + resolved:
        data = row.get("data") or {}
        rnd = int(_num(data.get("round"), 0))
        rounds_seen.add(rnd)
        endowment = max(endowment, _num(data.get("endowment"), 0.0))
        multiplier = max(multiplier, _num(data.get("multiplier"), 0.0))
        roster = max(roster, int(_num(data.get("group_size"), 0.0)))
        if row.get("label") == CONTRIBUTE_LABEL:
            cell[(str(row.get("source_user")), rnd)] = _num(data.get("contribution"), 0.0)

    players = sorted({player for player, _ in cell})
    roster = max(roster, len(players), 1)

    # Round count: episodes the engine actually ran (the final round has no
    # following update, so a fully silent last round would otherwise vanish from
    # the grid). The manifest's num_steps is the configured TARGET — only a
    # fallback, or an early-stopped run's un-run rounds would score as silence.
    observed = (max(rounds_seen) + 1) if rounds_seen else 0
    ran = _episodes_run(run_dir)
    n_rounds = max(ran if ran is not None else int(_num(manifest.get("num_steps"), 0.0)), observed)

    round_pool: dict[int, float] = defaultdict(float)
    per_player_total: dict[str, float] = defaultdict(float)
    total_contributed = 0.0
    for (player, rnd), amount in cell.items():
        round_pool[rnd] += amount
        per_player_total[player] += amount
        total_contributed += amount

    max_contrib = endowment * roster * n_rounds
    avg_contribution_rate = (total_contributed / max_contrib) if max_contrib > 0 else None

    # Collective payoff over the full round set (missing rounds resolve to pool 0):
    #   collective_r = N*E + (multiplier-1)*pool_r ;  floor_r = N*E ;  optimal_r = multiplier*N*E
    total_collective = 0.0
    total_floor = 0.0
    total_optimal = 0.0
    for rnd in range(n_rounds):
        pool = round_pool.get(rnd, 0.0)
        floor = endowment * roster
        total_floor += floor
        total_optimal += multiplier * endowment * roster
        total_collective += floor + (multiplier - 1.0) * pool
    span = total_optimal - total_floor
    efficiency = ((total_collective - total_floor) / span) if span > 0 else None
    pct_of_optimal = (total_collective / total_optimal) if total_optimal > 0 else None

    # Per-player rate over ALL rounds (missing rounds = 0); non-acting players are
    # full free-riders counted in both numerator and denominator.
    per_capita_rounds = endowment * n_rounds
    per_player_rate = {
        player: (per_player_total[player] / per_capita_rounds if per_capita_rounds > 0 else 0.0)
        for player in players
    }
    acting_free_riders = sum(1 for rate in per_player_rate.values() if rate < FREE_RIDER_THRESHOLD)
    non_acting = max(0, roster - len(players))
    free_rider_share = (acting_free_riders + non_acting) / roster if roster > 0 else None

    aggregated = {
        "avg_contribution_rate": avg_contribution_rate,
        "efficiency": efficiency,
        "collective_payoff": total_collective if n_rounds else None,
        "pct_of_optimal": pct_of_optimal,
        "free_rider_share": free_rider_share,
    }
    agents_out = {player: {"contribution_rate": rate} for player, rate in per_player_rate.items()}
    summary = {
        "roster": roster,
        "rounds": n_rounds,
        "contribution_events": len(cell),
        "total_contributed": total_contributed,
    }
    return {
        "source": str(run_dir),
        "aggregated": aggregated,
        "agents": agents_out,
        "summary": summary,
    }


def main() -> None:
    """Parse ``--run-dir``/``--output`` and write the eval JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", "-o", required=True)
    args = parser.parse_args()
    result = evaluate_run_dir(Path(args.run_dir))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
