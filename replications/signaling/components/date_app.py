"""The date channel: a messaging backend scoped to one dyad, one day.

``MessagingApp`` already provides everything the conversation phase needs —
private storage, participant/recipient/length validation, delivery as the
recipient's next observation, and checkpointing — so the subclass adds exactly
one thing: **dyad scoping**, in the two places where "who may I talk to" and
"what have we said" are decided.

Why scoping rather than a fresh backend per day: Concordia builds a whole new
simulation object per dyad per day (``dial.run_dyad_simulation``), so a
conversation never sees a previous day's raw dialogue — only the reflections
survive, carried in agent memory. Here the backend persists for the run, so the
same semantics come from rendering: ``observe`` shows only the messages
exchanged with *today's* partner during *today's* date phase. The full transcript
still exists in ``action_events.jsonl`` for analysis; it just is not context.

Both scoping decisions are pure functions of the step index (:mod:`calendar`)
and the roster (:mod:`dyads`), so nothing here holds state and the backend stays
replay- and resume-stable under the concurrent multi-GM traversal, where several
dyads speak in the same step.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from replications.signaling.components import dyads
from replications.signaling.components.calendar import DayCalendar
from silisocs.environments.backends.messaging.app import MessagingApp


@dataclass
class DateMessagingApp(MessagingApp):
    """Private messages, restricted to the day's date partner and date phase."""

    #: Path to ``input/personas_la.json`` — the buyer roster and the sex map the
    #: dyad schedule is generated from (sellers never appear in it).
    personas_path: str = ""
    seed: int = 42
    num_days: int = 5
    #: The run's single day layout (``calendar: ${calendar}``), replaced in
    #: ``__post_init__`` by the :class:`DayCalendar` it describes.
    calendar: Any = None

    def __post_init__(self) -> None:
        """Build the shared calendar on top of the base messaging state."""
        super().__post_init__()
        self._calendar = DayCalendar.from_config(self.calendar, component=type(self).__name__)
        self.calendar = self._calendar
        self._buyers: list[str] = []
        self._schedule: dyads.Schedule = {}

    def name(self) -> str:
        """Return the backend name."""
        return "signaling_date"

    # ---- setup ----------------------------------------------------------

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Initialize the message log, then bind the buyers and dyad schedule."""
        super().initialize(agent_names, **kwargs)
        self._bind_roster(agent_names)

    def _bind_roster(self, agent_names: Sequence[str]) -> None:
        """Derive buyers and the dyad schedule from a roster."""
        self._buyers = dyads.buyer_roster([str(name) for name in agent_names], self.personas_path)
        # A calendar with no date turns schedules no dyads at all.
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
        """Who this agent is on a date with on the step's day; "" if nobody."""
        day = self._calendar.day_of(step)
        return dyads.partners_for_day(self._schedule, day).get(str(agent_name), "")

    # ---- dyad scoping ---------------------------------------------------

    def _ensure_recipient(self, agent_name: str, recipient: str) -> str | None:
        """Reject anything but a message to today's partner during today's date."""
        base_error = super()._ensure_recipient(agent_name, recipient)
        if base_error:
            return base_error
        step = self.current_episode()
        if not self._calendar.is_date_step(step):
            return "The date has not started; there is nobody to talk to yet."
        partner = self.partner_of(agent_name, step)
        if not partner:
            return "You are not on a date today, so there is nobody to talk to."
        if str(recipient) != partner:
            return (
                f"You are on a date with {partner}. {recipient} is not here — "
                f"you can only speak to {partner}."
            )
        return None

    # ---- observation ----------------------------------------------------

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        """Render only today's conversation with today's partner.

        Not delegated to the base ``observe``: that one renders the actor's whole
        mailbox plus broadcasts, which would leak previous days' dyads into
        context and break the fresh-conversation-per-day semantics this port is
        reproducing. There are no broadcasts on this backend by design.

        The day scope IS the history window here — the whole point is that a
        dyad sees its complete transcript and nothing older — so
        ``history_window`` is not applied; an observe component may still pass an
        explicit ``limit`` to trim the rendered tail.
        """
        step = self.current_episode()
        phase = self._calendar.phase_of(step)
        partner = self.partner_of(actor_name, step)
        if not self._calendar.is_date_step(step) or not partner:
            return "DATE\nYou are not on a date right now."
        pair = {str(actor_name), partner}
        visible = [
            row
            for row in self._messages
            if self._calendar.is_date_step(row["round"])
            and self._calendar.day_of(row["round"]) == phase.day
            and {row["sender"], row["recipient"]} == pair
        ]
        limit = int(kwargs.get("limit") or 0)
        if limit > 0:
            visible = visible[-limit:]
        header = (
            f"DATE — day {phase.day + 1}, turn {phase.position + 1} of {self._calendar.dial_turns}\n"
            f"You are {actor_name}, on a first date with {partner}."
        )
        if not visible:
            return f"{header}\n\nThe conversation is just beginning; nothing has been said yet."
        lines = ["Your conversation so far:"]
        for row in visible:
            speaker = "You" if row["sender"] == str(actor_name) else partner
            lines.append(f"  {speaker}: {row['text']}")
        return f"{header}\n\n" + "\n".join(lines)

    # ---- checkpoint contract -------------------------------------------
    #
    # No override: this subclass adds no mutable state. ``_calendar`` is frozen
    # config, and ``_buyers``/``_schedule`` are pure functions of the roster that
    # ``initialize`` rebuilds on every run — including a resumed one, where the
    # GM initializer runs before the checkpoint's ``set_state``. So the inherited
    # ``MessagingApp.get_state``/``set_state`` (messages, participants, and the
    # committed-events mirror) round-trip this backend completely.
