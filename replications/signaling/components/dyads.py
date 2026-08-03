"""Mixed-sex dyad scheduling — a verbatim port of the upstream pairing rule.

``generate_mixed_sex_dates`` below reproduces
``examples/signaling/configs/personas.py::generate_mixed_sex_dates`` exactly:
the same ``random.seed(seed)``, the same ``random.shuffle`` of the female list
inside a retry loop, and the same "no pair may repeat across days" rejection
test. Given the same roster order and seed it returns the *identical* schedule,
which is why the port's dyad test is a strict equality check against upstream
rather than a property check.

The schedule is a pure function of ``(names, sexes, num_days, seed)``, so every
component that needs it (the date GM's next-acting, the journal GM's reflection
prompts, the day-boundary handler) derives it independently instead of sharing
mutable state.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path

Dyad = tuple[str, str]
Schedule = dict[int, list[Dyad]]


def load_persona_records(path: str | Path) -> list[dict]:
    """Load ``input/personas_la.json`` (list of ``{name, sex, context, memories}``)."""
    with open(path, encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        raise TypeError(f"{path}: expected a list of persona records.")
    return records


def load_sex_map(path: str | Path) -> dict[str, str]:
    """Map persona name -> sex, from the converted persona file."""
    return {str(r["name"]): str(r.get("sex", "")) for r in load_persona_records(path)}


@lru_cache(maxsize=8)
def _persona_names(personas_path: str) -> frozenset[str]:
    return frozenset(str(r["name"]) for r in load_persona_records(personas_path))


def buyer_roster(agent_names: Sequence[str], personas_path: str | Path) -> list[str]:
    """Return the buyers among a GM's roster, in roster order.

    Every component that needs the dyad schedule derives the roster this way —
    membership in the converted persona file — so the market GM, the date GM's
    next-acting, the journal prompts, and the day-boundary handler all agree on
    the ordering the schedule is generated from without sharing state. Sellers
    are generated from the goods table and never appear in the persona file.
    """
    known = _persona_names(str(personas_path))
    return [str(name) for name in agent_names if str(name) in known]


def generate_mixed_sex_dates(
    names: Sequence[str],
    num_days: int,
    sexes: Mapping[str, str],
    seed: int = 42,
) -> tuple[Schedule, list[str]]:
    """Generate mixed-sex date pairings for each day (verbatim upstream logic).

    Ported from ``personas.generate_mixed_sex_dates``; the only change is that
    the sex lookup is passed in rather than read from a module-level constant,
    so the port's roster can differ from upstream's 50-name table.
    """
    # Verbatim upstream: reseeds the GLOBAL random module. Safe here only
    # because build_schedule memoizes the result and every caller resolves the
    # same cache key during single-threaded construction/update phases — do not
    # call this generator directly from concurrent turn code.
    random.seed(seed)
    males = [name for name in names if sexes.get(name) == "male"]
    females = [name for name in names if sexes.get(name) == "female"]
    all_dates: Schedule = {}
    seen_pairs: set[Dyad] = set()
    num_possible_pairs = min(len(males), len(females))
    if len(males) > len(females):
        leftovers = males[num_possible_pairs:]
    else:
        leftovers = females[num_possible_pairs:]
    males_to_pair = males[:num_possible_pairs]
    females_to_pair = females[:num_possible_pairs]
    max_attempts_per_day = 1000
    for day in range(num_days):
        for _ in range(max_attempts_per_day):
            random.shuffle(females_to_pair)
            candidate_dyads = list(zip(males_to_pair, females_to_pair, strict=False))
            has_collision = any(tuple(sorted(p)) in seen_pairs for p in candidate_dyads)
            if not has_collision:
                all_dates[day] = candidate_dyads
                for p in candidate_dyads:
                    seen_pairs.add(tuple(sorted(p)))  # type: ignore[arg-type]
                break
        else:
            raise ValueError(
                f"Could not find a unique set of pairings for day {day}. "
                "All possible combinations might be exhausted."
            )
    return all_dates, leftovers


@lru_cache(maxsize=32)
def _cached_schedule(
    names: tuple[str, ...],
    num_days: int,
    personas_path: str,
    seed: int,
) -> tuple[Schedule, tuple[str, ...]]:
    sexes = load_sex_map(personas_path)
    schedule, leftovers = generate_mixed_sex_dates(list(names), num_days, sexes, seed)
    return schedule, tuple(leftovers)


def build_schedule(
    buyer_names: Sequence[str],
    *,
    num_days: int,
    personas_path: str | Path,
    seed: int,
) -> Schedule:
    """Return ``{day: [(p1, p2), ...]}`` for this run's buyer roster.

    Memoized on its arguments because several components ask for the same
    schedule every step; the underlying generator reseeds ``random`` globally,
    so recomputing it repeatedly would also be an avoidable side effect.
    """
    schedule, _ = _cached_schedule(tuple(buyer_names), int(num_days), str(personas_path), int(seed))
    return {day: list(pairs) for day, pairs in schedule.items()}


def partners_for_day(schedule: Schedule, day: int) -> dict[str, str]:
    """Flatten one day's dyads into a symmetric ``{agent: partner}`` map."""
    partners: dict[str, str] = {}
    for first, second in schedule.get(day, []):
        partners[first] = second
        partners[second] = first
    return partners


def speakers_for_turn(schedule: Schedule, day: int, turn_index: int) -> list[str]:
    """Who speaks on date turn ``turn_index`` of ``day``: one member per dyad.

    Speakers alternate so a reply always follows the partner's utterance, which
    is what makes a step-per-turn schedule a real conversation; dyads advance
    concurrently because they are causally independent (upstream runs them
    serially only because it rebuilds a simulation object per dyad).
    """
    which = int(turn_index) % 2
    return [pair[which] for pair in schedule.get(day, [])]
