"""A loop strategy that checks the run's declared length against the calendar.

``num_steps`` and ``day_length`` are the two numbers the day calendar implies but
cannot compute *in* config: OmegaConf does no arithmetic, and the framework reads
``num_steps`` before any of the replication's code is importable. So they are
written out in the world config — and this strategy is what stops them from
drifting.

The check belongs here because the loop is what consumes ``max_steps``. It runs
at the top of the run, before a single model call: a mismatch means the
experiment would silently stop mid-day (or run past its last one), which is worse
than a crash and much harder to notice in the artifacts.

A committed-config test could not do this job alone. ``num_steps`` is also wrong
whenever someone overrides a calendar field on the command line —
``calendar.rounds_per_day=3`` composes cleanly and truncates the experiment — and
the check catches exactly that case, because it reads the same composed config
the run does.

Everything else is inherited: probes, interventions, checkpoints, and interactive
control all keep their default behaviour.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from replications.signaling.components.calendar import DayCalendar
from silisocs.simulation_engines.policies.loops import FixedStepsLoopStrategy


class CalendarLoopStrategy(FixedStepsLoopStrategy):
    """``fixed_steps``, with the declared run length validated against the calendar."""

    name = "signaling_calendar"

    def __init__(
        self,
        *,
        calendar: Mapping[str, Any] | None = None,
        num_days: int = 5,
        day_length: int | None = None,
    ) -> None:
        self._calendar = DayCalendar.from_config(calendar, component=type(self).__name__)
        self._num_days = int(num_days)
        self._day_length = None if day_length is None else int(day_length)

    def run(  # noqa: PLR0913 - the LoopStrategy protocol's signature
        self,
        *,
        engine: Any,
        game_masters: list[Any],
        agents: list[Any],
        max_steps: int,
        start_step: int,
        verbose: bool,
        checkpoint_callback: Any | None,
    ) -> None:
        """Validate the declared step counts, then run the normal episode loop."""
        self._check(int(max_steps))
        super().run(
            engine=engine,
            game_masters=game_masters,
            agents=agents,
            max_steps=max_steps,
            start_step=start_step,
            verbose=verbose,
            checkpoint_callback=checkpoint_callback,
        )

    def _check(self, max_steps: int) -> None:
        """Raise when ``num_steps``/``day_length`` disagree with the calendar."""
        expected_steps = self._calendar.num_steps(self._num_days)
        if max_steps != expected_steps:
            raise ValueError(
                f"num_steps={max_steps} does not match the day calendar: "
                f"{self._num_days} days x {self._calendar.day_length} steps/day = "
                f"{expected_steps}. The calendar is "
                f"rounds_per_day={self._calendar.rounds_per_day} + 1 market reflection "
                f"+ 1 dial setup + dial_turns={self._calendar.dial_turns} "
                f"+ date_reflections={self._calendar.date_reflections}. "
                f"Set num_steps: {expected_steps} in the world config (or override it "
                "on the command line alongside the calendar change)."
            )
        if self._day_length is not None and self._day_length != self._calendar.day_length:
            raise ValueError(
                f"day_length={self._day_length} does not match the day calendar's "
                f"{self._calendar.day_length}. It is only used for the checkpoint "
                f"cadence; set day_length: {self._calendar.day_length}."
            )
