"""The day calendar: the pure function that turns steps into experiment phases.

SiliSocS's unit is the **step** (= backend episode); the signaling experiment's
unit is the **day**. This module is the whole bridge. A day is a fixed block of
steps:

``[market] * R  +  [market_reflection]  +  [dial_setup]  +  [date] * D  +
[date_reflection] * K``

so ``phase_of(step)`` is a pure function of the step index and the four
lengths — no state, no counters, hence replay- and resume-stable, and safe to
consult concurrently from several components.

Every phase is owned by exactly one game master, which is what makes the
default concurrent ``multi_gm`` traversal correct without a custom step
strategy: on any step, the off-phase GMs' next-acting components return an
empty roster and their chain hops cost nothing.

============================  ==========================  =====================
Phase                         Acting GM                   Who acts
============================  ==========================  =====================
``market``                    ``market_gm``               buyers + sellers
``market_reflection``         ``journal_gm``              buyers
``dial_setup``                *(nobody acts)*             the day-boundary
                                                          ceremony (``market_gm``'s
                                                          update component)
``date``                      ``date_gm``                 one speaker per dyad
``date_reflection``           ``journal_gm``              buyers
============================  ==========================  =====================

The ``dial_setup`` step carries no turns: it is the step
``DayBoundaryUpdateComponent`` recognises, so the eating/wearing draw and the
LLM-generated personal events and date scene all land after the day's market
reflection and before its first conversational turn — upstream's ordering.
Keeping the slot in every condition (rather than only when dates run) means the
three conditions share one calendar shape, which keeps day indices comparable
across arms.

Condition shapes (defaults ``R=5``, ``D=80``, ``K=4``):

* ``social``            — ``D=80``, ``K=4``  → 91 steps/day
* ``asocial_personal``  — ``D=0``,  ``K=0``  → 7 steps/day (setup injects the
  personal events only)
* ``asocial``           — ``D=0``,  ``K=0``  → 7 steps/day (no generated
  content; the step still runs the unconditional daily eating draw)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

PHASE_MARKET = "market"
PHASE_MARKET_REFLECTION = "market_reflection"
PHASE_DIAL_SETUP = "dial_setup"
PHASE_DATE = "date"
PHASE_DATE_REFLECTION = "date_reflection"

#: Ordered labels for the post-date reflection steps. Verbatim in meaning from
#: ``dial.run_dyad_simulation``: summarize the date, describe the visual first
#: impression, compare against other potential partners, then rate 0-10.
DATE_REFLECTION_KINDS: tuple[str, ...] = (
    "date_summary",
    "visual_impression",
    "comparison",
    "rating",
)


class Phase(NamedTuple):
    """Where one step sits in the experiment.

    ``position`` is where the step sits *within* the phase — the market round
    number, or which of the date turns / post-date reflections this is. ``kind``
    names a reflection step (see :data:`DATE_REFLECTION_KINDS`) and is empty
    elsewhere. (Named ``position`` rather than ``index`` so it does not shadow
    ``tuple.index``.)
    """

    day: int
    phase: str
    position: int
    kind: str = ""


@dataclass(frozen=True)
class DayCalendar:
    """Fixed-length day layout shared by every component of the replication."""

    rounds_per_day: int = 5
    dial_turns: int = 80
    date_reflections: int = 4

    def __post_init__(self) -> None:
        """Validate the day layout."""
        if int(self.rounds_per_day) < 1:
            raise ValueError("DayCalendar.rounds_per_day must be >= 1.")
        if int(self.dial_turns) < 0:
            raise ValueError("DayCalendar.dial_turns must be >= 0.")
        if not 0 <= int(self.date_reflections) <= len(DATE_REFLECTION_KINDS):
            raise ValueError(
                "DayCalendar.date_reflections must be between 0 and "
                f"{len(DATE_REFLECTION_KINDS)} (the upstream reflection sequence)."
            )
        if int(self.dial_turns) == 0 and int(self.date_reflections) != 0:
            raise ValueError(
                "DayCalendar with no date turns cannot schedule post-date "
                "reflections; set date_reflections: 0 alongside dial_turns: 0."
            )

    @classmethod
    def from_config(cls, params: Mapping[str, Any] | None, *, component: str) -> DayCalendar:
        """Build from a component's ``calendar:`` param, requiring it to be present.

        Every component of the run must agree on the day layout — the phases only
        line up because they all consult the same numbers. Defaulting a missing
        block would give a component the *social* shape (80 date turns) inside an
        asocial run, and the disagreement would surface as a GM that silently
        never acts. So an absent block is an error naming the fix.
        """
        if not params:
            raise ValueError(
                f"{component} needs a `calendar:` block — write `calendar: ${{calendar}}` "
                "in its params so it shares the run's single day layout."
            )
        return cls.from_params(params)

    @classmethod
    def from_params(cls, params: Mapping[str, Any] | None) -> DayCalendar:
        """Build from a YAML ``calendar:`` block, ignoring unset keys."""
        values = {
            key: int(value)
            for key, value in dict(params or {}).items()
            if key in {"rounds_per_day", "dial_turns", "date_reflections"} and value is not None
        }
        unknown = sorted(
            set(dict(params or {}))
            - {
                "rounds_per_day",
                "dial_turns",
                "date_reflections",
            }
        )
        if unknown:
            raise ValueError(
                f"Unknown calendar key(s): {unknown}. Supported: rounds_per_day, "
                "dial_turns, date_reflections."
            )
        return cls(**values)

    # ---- layout ---------------------------------------------------------

    @property
    def day_length(self) -> int:
        """Number of steps in one simulated day."""
        return int(self.rounds_per_day) + 1 + 1 + int(self.dial_turns) + int(self.date_reflections)

    @property
    def runs_dates(self) -> bool:
        """Whether this calendar schedules conversational turns at all."""
        return int(self.dial_turns) > 0

    def num_steps(self, num_days: int) -> int:
        """Total engine steps needed to run ``num_days`` days."""
        return int(num_days) * self.day_length

    def day_of(self, step: int) -> int:
        """Which day a step belongs to."""
        return int(step) // self.day_length

    def phase_of(self, step: int) -> Phase:
        """Decompose a step index into (day, phase, index-within-phase, kind)."""
        step = int(step)
        if step < 0:
            raise ValueError(f"step must be >= 0 (got {step}).")
        day, offset = divmod(step, self.day_length)
        rounds = int(self.rounds_per_day)
        if offset < rounds:
            return Phase(day, PHASE_MARKET, offset)
        offset -= rounds
        if offset == 0:
            return Phase(day, PHASE_MARKET_REFLECTION, 0, "market_reflection")
        offset -= 1
        if offset == 0:
            return Phase(day, PHASE_DIAL_SETUP, 0)
        offset -= 1
        if offset < int(self.dial_turns):
            return Phase(day, PHASE_DATE, offset)
        offset -= int(self.dial_turns)
        return Phase(day, PHASE_DATE_REFLECTION, offset, DATE_REFLECTION_KINDS[offset])

    # ---- convenience predicates ----------------------------------------

    def is_market_step(self, step: int) -> bool:
        """Whether a step is one of the day's simultaneous market rounds."""
        return self.phase_of(step).phase == PHASE_MARKET

    def is_journal_step(self, step: int) -> bool:
        """Whether a step is a reflection step (market or post-date)."""
        return self.phase_of(step).phase in {PHASE_MARKET_REFLECTION, PHASE_DATE_REFLECTION}

    def is_date_step(self, step: int) -> bool:
        """Whether a step is a conversational turn."""
        return self.phase_of(step).phase == PHASE_DATE

    def reflection_kind(self, step: int) -> str:
        """Label for a reflection step (empty when the step is not one)."""
        phase = self.phase_of(step)
        return phase.kind if self.is_journal_step(step) else ""

    # ---- anchors --------------------------------------------------------

    def day_start_step(self, day: int) -> int:
        """First step (market round 0) of a day."""
        return int(day) * self.day_length

    def dial_setup_step(self, day: int) -> int:
        """Return the step the day-boundary ceremony runs on."""
        return self.day_start_step(day) + int(self.rounds_per_day) + 1

    def dial_setup_steps(self, num_days: int) -> list[int]:
        """Every day's ceremony step.

        Nothing in the runtime needs this — the ceremony recognises its own step
        from :meth:`phase_of` — but it states the schedule explicitly, which is
        what the calendar tests assert against.
        """
        return [self.dial_setup_step(day) for day in range(int(num_days))]
