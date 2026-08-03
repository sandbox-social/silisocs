"""Calendar-gated next-acting components — the seam that turns steps into phases.

The signaling experiment's phases are *time*-based, not agent-based: on step
``s`` the market runs, or the journal runs, or the dates run. The one seam that
expresses "who acts at this game master at this step" is the GM's ``next_acting``
slot, so the whole phase schedule is two components here plus the pure
:mod:`calendar` function they consult — no custom engine, step strategy, or
``flow_order`` trickery.

Because every phase is owned by exactly one GM, at most one GM's roster is
non-empty on any step and the off-phase GMs' chain hops cost nothing. That is
what makes the default concurrent ``multi_gm`` traversal correct here.

Both components are **stateless**: their answer is a pure function of the step
index (read from the backend), the configured phases, and the roster. Nothing is
carried between calls, so they need no ``get_state``/``set_state``, are
replay- and resume-stable, and are safe to call concurrently under the
concurrent traversal. ``DyadTurnNextActing`` memoizes the dyad schedule, but the
schedule is itself a pure function of ``(roster, num_days, personas, seed)`` —
a cache, not state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from replications.signaling.components import dyads
from replications.signaling.components.calendar import (
    PHASE_DATE,
    PHASE_DATE_REFLECTION,
    PHASE_DIAL_SETUP,
    PHASE_MARKET,
    PHASE_MARKET_REFLECTION,
    DayCalendar,
)
from silisocs.environments.gm.components.base import NextActingComponent

#: Every phase name a config may name, in calendar order.
VALID_PHASES: tuple[str, ...] = (
    PHASE_MARKET,
    PHASE_MARKET_REFLECTION,
    PHASE_DIAL_SETUP,
    PHASE_DATE,
    PHASE_DATE_REFLECTION,
)

#: ``roles``: the whole GM roster (market GM: buyers + sellers) ...
ROLE_ALL = "all"
#: ... or only the buyers, i.e. the members of the converted persona file.
ROLE_BUYERS = "buyers"

VALID_ROLES: tuple[str, ...] = (ROLE_ALL, ROLE_BUYERS)


def current_step(backend: Any) -> int:
    """Read the engine's current episode index off a backend.

    ``current_episode()`` is the documented seam; the ``action_logger`` fallback
    keeps the components usable against a duck-typed stand-in that only carries
    the stamped logger.
    """
    current_episode = getattr(backend, "current_episode", None)
    if callable(current_episode):
        return int(current_episode() or 0)
    return int(getattr(getattr(backend, "action_logger", None), "episode_idx", 0) or 0)


def calendar_from_params(params: Mapping[str, Any] | None, *, component: str) -> DayCalendar:
    """Build the shared day calendar from a component's ``calendar:`` param."""
    return DayCalendar.from_config(params, component=component)


def _require_backend(context: Any, component: str) -> Any:
    backend = getattr(context, "backend", None)
    if backend is None:
        raise ValueError(
            f"{component} needs its game master's context: the backend carries the "
            "episode index every calendar decision is keyed by."
        )
    return backend


def _validate_phases(phases: Sequence[str] | str, component: str) -> frozenset[str]:
    """Parse and validate the configured phase names.

    A typo here would silently mean "never act" — the GM would simply do nothing
    for the whole run — so an unknown (or empty) phase list is a build-time error
    that names the valid options.
    """
    raw = [phases] if isinstance(phases, str) else list(phases or [])
    names = [str(phase).strip() for phase in raw if str(phase).strip()]
    valid = ", ".join(VALID_PHASES)
    if not names:
        raise ValueError(
            f"{component} needs a non-empty `phases` list; an empty one would mean "
            f"this game master never acts. Valid phases: {valid}."
        )
    unknown = sorted({name for name in names if name not in VALID_PHASES})
    if unknown:
        raise ValueError(f"{component} got unknown calendar phase(s) {unknown}. Valid: {valid}.")
    return frozenset(names)


class CalendarNextActing(NextActingComponent):
    """Select this game master's whole roster on the calendar phases it owns.

    Used by ``market_gm`` (``phases: [market]``, ``roles: all`` — buyers and
    sellers submit simultaneously) and by ``journal_gm``
    (``phases: [market_reflection, date_reflection]``, ``roles: buyers`` — only
    personas reflect). Stateless: the roster is fixed at build time and the
    gate is ``calendar.phase_of(step).phase in phases``, a pure function of the
    step, so the component is replay-, resume-, and concurrency-safe.
    """

    def __init__(  # noqa: PLR0913 - every argument is a documented config knob
        self,
        *,
        agent_names: Sequence[str] = (),
        context: Any = None,
        phases: Sequence[str] | str = (),
        roles: str = ROLE_ALL,
        personas_path: str = "",
        calendar: Mapping[str, Any] | None = None,
        # The next_acting slot also passes `sim_roles` and `sequence`; neither is
        # meaningful for a calendar gate.
        **_ignored: Any,
    ) -> None:
        super().__init__()
        component = type(self).__name__
        self._backend = _require_backend(context, component)
        self._calendar = calendar_from_params(calendar, component=component)
        self._phases = _validate_phases(phases, component)

        role = str(roles or ROLE_ALL).strip().lower()
        if role not in VALID_ROLES:
            raise ValueError(f"{component} got roles='{roles}'. Valid: {', '.join(VALID_ROLES)}.")
        names = [str(name) for name in agent_names]
        if role == ROLE_BUYERS:
            if not str(personas_path).strip():
                raise ValueError(
                    f"{component} with roles='{ROLE_BUYERS}' needs `personas_path`: "
                    "buyers are exactly the members of the converted persona file."
                )
            names = dyads.buyer_roster(names, str(personas_path))
        self._roster = names

    def acting_agent_names(self) -> list[str]:
        """Return the roster on an owned phase, and nobody otherwise."""
        if self._calendar.phase_of(current_step(self._backend)).phase not in self._phases:
            return []
        return list(self._roster)


class DyadTurnNextActing(NextActingComponent):
    """Select one speaker per dyad on a date step, alternating between turns.

    On date turn ``k`` of day ``d`` every dyad's member ``k % 2`` speaks, so a
    reply always follows the partner's utterance while the dyads — causally
    independent — advance concurrently within the step. Stateless: the speaker
    set is ``speakers_for_turn(schedule, day, turn)``, a pure function of the
    step and the (pure, seed-derived) dyad schedule, hence replay- and
    resume-stable under the concurrent traversal.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        agent_names: Sequence[str] = (),
        context: Any = None,
        personas_path: str = "",
        seed: int = 42,
        num_days: int = 5,
        calendar: Mapping[str, Any] | None = None,
        **_ignored: Any,
    ) -> None:
        super().__init__()
        component = type(self).__name__
        self._backend = _require_backend(context, component)
        self._calendar = calendar_from_params(calendar, component=component)
        self._personas_path = str(personas_path).strip()
        if not self._personas_path:
            raise ValueError(
                f"{component} needs `personas_path`: the dyad schedule is generated "
                "from the persona file's names and sexes."
            )
        self._seed = int(seed)
        self._num_days = int(num_days)
        self._agent_names = [str(name) for name in agent_names]
        self._schedule: dyads.Schedule | None = None

    def _dyad_schedule(self) -> dyads.Schedule:
        """Return the run's dyad schedule, built once (a memoized pure function)."""
        if self._schedule is None:
            self._schedule = dyads.build_schedule(
                dyads.buyer_roster(self._agent_names, self._personas_path),
                num_days=self._num_days,
                personas_path=self._personas_path,
                seed=self._seed,
            )
        return self._schedule

    def acting_agent_names(self) -> list[str]:
        """Return this date turn's speakers, and nobody off a date step."""
        phase = self._calendar.phase_of(current_step(self._backend))
        if phase.phase != PHASE_DATE:
            return []
        return dyads.speakers_for_turn(self._dyad_schedule(), phase.day, phase.position)
