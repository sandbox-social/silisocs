"""The reflection surface: a backend whose only action is "write it down".

Upstream, the five reflections of a day (one on the marketplace, four on the
date) are bare ``agent.act(free_action_spec(...))`` + ``agent.observe('[Reflection] …')``
calls inside the driver loop — ``simulation.run_simulation`` for the market one,
``dial.run_dyad_simulation`` for the four post-date ones. That makes them
invisible to the run's artifacts and strictly serial across the roster.

Here each reflection is an ordinary engine step against this backend, which buys
three things for ~150 lines: the ~50 reflection calls of a boundary run
*concurrently* under the normal executor, one committed row per reflection in
``action_events.jsonl`` (so evaluators read reflections the same way they read
trades — no bespoke log file), and checkpoint round-tripping for free.

The action *message* is upstream's memory feedback. ``record_reflection``
returns the reflection wrapped in its upstream memory tag (``[marketplace
reflection] ...`` / ``[Reflection] ...`` / the rated form), and the engine
observes every resolve message back into the acting agent — so the tagged text
re-enters the agent's memory exactly where upstream calls
``agent.observe('[Reflection] ...')``. That feedback is the experiment's
cross-day social memory: it is how day N's date can be compared against day
N-1's.

The backend deliberately holds no prompt text. *Which* question is asked at a
given step is the action-prompt component's job (it reads
``prompts.REFLECTION_PROMPTS`` keyed by the calendar); this backend only decides
whether the step is a reflection step at all, labels the answer with the
calendar's kind, and — on the rating step — applies upstream's rating regex.
Everything it needs is a pure function of the step index (:mod:`calendar`) and
the roster (:mod:`dyads`), so it keeps no counters and is replay- and
resume-stable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from replications.signaling.components import dyads, prompts
from replications.signaling.components.calendar import (
    DATE_REFLECTION_KINDS,
    PHASE_MARKET_REFLECTION,
    DayCalendar,
)
from silisocs.environments.backends.base import ActionResult, BackendApp, app_action
from silisocs.runtime.telemetry.collector import SimMetricsCollector

#: Reflection kinds are the calendar's own labels: the market-reflection step
#: reports its phase name, the four post-date steps report DATE_REFLECTION_KINDS.
_MARKET_KIND = PHASE_MARKET_REFLECTION
_RATING_KIND = "rating"


@dataclass
class JournalApp(BackendApp):
    """A private journal: agents record one free-text reflection per journal step."""

    action_logger: Any = None
    provides_checkpoint_state = True
    app_description: str = (
        "Your private journal. Record today's reflection here; nobody else reads it."
    )
    #: Path to ``input/personas_la.json`` — the buyer roster and the sex map the
    #: dyad schedule is generated from (sellers never appear in it).
    personas_path: str = ""
    seed: int = 42
    num_days: int = 5
    #: The run's single day layout, configured as ``calendar: ${calendar}``.
    #: Every component of the run is built from that one block — the agreement is
    #: what makes the phases line up. ``__post_init__`` replaces the mapping with
    #: the :class:`DayCalendar` it describes, so ``backend.calendar`` is the object
    #: readers (the day-boundary ceremony, the evaluators) actually want.
    calendar: Any = None

    def __post_init__(self) -> None:
        """Build the shared calendar and the empty roster."""
        super().__init__()
        self._calendar = DayCalendar.from_config(self.calendar, component=type(self).__name__)
        self.calendar = self._calendar
        self._participants: list[str] = []
        self._buyers: list[str] = []
        self._schedule: dyads.Schedule = {}

    # ---- identity -------------------------------------------------------

    def name(self) -> str:
        """Return the backend name."""
        return "signaling_journal"

    def description(self) -> str:
        """Return the agent-facing description of the journal."""
        return self.app_description

    # ---- setup ----------------------------------------------------------

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Bind the roster (buyers + dyad schedule)."""
        del kwargs
        self._bind_roster(agent_names)

    def _bind_roster(self, agent_names: Sequence[str]) -> None:
        """Derive buyers and the dyad schedule from a roster (also used on restore)."""
        self._participants = [str(name) for name in agent_names]
        self._buyers = dyads.buyer_roster(self._participants, self.personas_path)
        # An asocial arm runs no dates, so it has no dyads to schedule; asking for
        # one would fail once the roster is too small to pair.
        self._schedule = (
            dyads.build_schedule(
                self._buyers,
                num_days=int(self.num_days),
                personas_path=self.personas_path,
                seed=int(self.seed),
            )
            if self._calendar.runs_dates
            else {}
        )

    def partner_of(self, agent_name: str, step: int) -> str:
        """Who this agent is (or was) on a date with on the step's day; "" if nobody."""
        day = self._calendar.day_of(step)
        return dyads.partners_for_day(self._schedule, day).get(str(agent_name), "")

    # ---- actions --------------------------------------------------------

    @app_action(
        selectable_name="RECORD_REFLECTION",
        description=(
            "Write today's reflection in your journal, answering the question you were"
            " asked, in your own words."
        ),
        tags=("reflection",),
        fields={"reflection.kind": "kind", "reflection.text": "text"},
    )
    def record_reflection(self, agent_name: str, text: str = "") -> ActionResult:
        """Record one free-text reflection for the current journal step.

        The returned message is the reflection wrapped in its upstream memory
        tag; the engine observes it back into the acting agent, which is the
        feed-back-into-memory step of ``simulation.run_experiment`` /
        ``dial.run_dyad_simulation``.
        """
        if not str(text).strip():
            return ActionResult("A reflection cannot be empty.", committed=False)
        step = self.current_episode()
        kind = self._calendar.reflection_kind(step)
        if not kind:
            return ActionResult(
                "There is nothing to reflect on right now; the journal is closed.",
                committed=False,
            )
        day = self._calendar.day_of(step)
        # The market reflection is about the day's trading, not about a person.
        partner = "" if kind == _MARKET_KIND else self.partner_of(agent_name, step)
        data: dict[str, Any] = {
            "round": step,
            "day": day,
            "kind": kind,
            "partner": partner,
            "text": str(text),
        }
        if kind == _MARKET_KIND:
            memory = prompts.MARKET_REFLECTION_MEMORY.format(text=str(text))
        else:
            memory = prompts.REFLECTION_MEMORY.format(text=str(text))
        if kind == _RATING_KIND:
            raw_rating = self._extract_rating(str(text))
            data["rating"] = None if raw_rating is None else float(raw_rating)
            if raw_rating is not None:
                # Upstream interpolates the regex match itself, not a float.
                memory = prompts.RATING_MEMORY.format(
                    name=agent_name, partner=partner, rating=raw_rating
                )
        return ActionResult(memory, data=data)

    @staticmethod
    def _extract_rating(text: str) -> str | None:
        """Pull the 0-10 rating out of free text with upstream's regex.

        ``dial.run_dyad_simulation`` takes the FIRST number in the answer and
        falls back to storing the raw text when there is none; the fallback is
        counted rather than swallowed, so a model that stops emitting numbers
        shows up as a degraded run instead of a silently empty rating column.
        Returns the raw matched string (upstream's ``match.group(0)``).
        """
        match = re.search(prompts.RATING_PATTERN, text)
        if match is None:
            SimMetricsCollector.get().increment_counter("signaling_unparsed_ratings")
            return None
        return match.group(0)

    # ---- observation ----------------------------------------------------

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        """Frame the reflection: which day, which reflection, and about whom.

        Deliberately not the question itself — the action-prompt component owns
        the verbatim prompt text (``prompts.REFLECTION_PROMPTS``), so the two
        never drift apart or say the same thing twice.
        """
        del kwargs
        step = self.current_episode()
        phase = self._calendar.phase_of(step)
        kind = self._calendar.reflection_kind(step)
        if not kind:
            return "JOURNAL\nYour journal is closed right now."
        lines = [f"JOURNAL — day {phase.day + 1}"]
        if kind == _MARKET_KIND:
            lines.append("The marketplace has closed for the day.")
        else:
            partner = self.partner_of(actor_name, step)
            lines.append(
                f"Your date with {partner} has ended." if partner else "Your evening has ended."
            )
            total = min(self._calendar.date_reflections, len(DATE_REFLECTION_KINDS))
            lines.append(f"This is reflection {phase.position + 1} of {total} about the date.")
        lines.append("Write your answer to the question you are asked, in your own words.")
        return "\n".join(lines)

    # ---- read API for evaluators ---------------------------------------

    def reflections_for(
        self, agent_name: str | None = None, kind: str | None = None
    ) -> list[dict[str, Any]]:
        """Return recorded reflections, optionally filtered by author and/or kind.

        Reads the framework's committed-events mirror — the same rows
        ``action_events.jsonl`` holds — so the journal keeps no reflection list
        of its own and a restored backend answers identically for free.
        """
        rows = []
        for event in self.iter_committed_events(
            labels={"record_reflection"},
            agent=None if agent_name is None else str(agent_name),
        ):
            row = {"agent": str(event.get("source_user", "")), **event["data"]}
            if kind is None or row.get("kind") == str(kind):
                rows.append(row)
        return rows

    # ---- checkpoint contract -------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Return serializable journal state for checkpoints.

        The reflections themselves live in the committed-events mirror, which
        the base helpers snapshot; everything else is rebuilt from the roster.
        """
        return {
            "participants": list(self._participants),
            "committed_events": self._committed_events_state(),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore journal state from a checkpoint payload."""
        if not isinstance(state, Mapping):
            raise TypeError("JournalApp checkpoint state must be a mapping.")
        # Roster-derived state (buyers, dyad schedule) is a pure function of the
        # participant list, so it is rebuilt rather than stored.
        self._bind_roster(state.get("participants", []))
        self._restore_committed_events(state.get("committed_events"))
