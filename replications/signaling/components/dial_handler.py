"""The day boundary: seller reset, eating, and the DIAL ceremony.

This is the one piece of the replication that has to reach *across* game
masters. The signaling channel is the clothing an agent bought in the
marketplace: what a buyer wears on the date is drawn from the market backend's
inventory, and the date scene the conversation GM runs is generated from both
partners' wearing statements. Upstream does this between two rebuilt simulation
objects (``simulation.run_experiment`` computes eating/wearing from the market
state, then ``dial.create_simulation_for_dyad`` hands them to
``DayInTheLifeInitializer``).

Here it is the market game master's ``update`` component, which the engine calls
once per step, for every GM, serially on the loop thread, before any agent acts
(``RuntimeEngine.run_step``). That gives the boundary work exactly what it
needs — the step index, every agent object, and its own backend — with no locks
and no custom engine.

**Why an update component rather than an intervention.** Both run at the same
single-threaded boundary with the same reach, but an intervention has to be told
*when* to fire: ``at_step: 6, 97, 188, ...``, one hand-computed entry per day,
recomputed whenever the calendar changes. An update component is called every
step and asks the calendar whether this is a boundary step, so the schedule is
derived rather than declared. Changing ``dial_turns`` needs no config edit at
all.

Because it owns the slot, the component is also responsible for the ordinary
per-step backend update the built-in ``app_update`` would have done; it runs
that first, unconditionally. On the ceremony step this doubles as the guarantee
that the day's final market round is cleared before anything reads inventory.

The component's three duties, and when they run:

* **Seller reset** (every day-start step, all conditions). Upstream rebuilds
  the marketplace config each morning and regenerates every seller as a fresh
  agent — full inventory, starting cash, no memory — while buyers carry
  everything over. The ledger half is ``backend.restock_sellers()``; the memory
  half restores each seller agent to the pristine state snapshotted at step 0
  (the duck-typed ``get_state``/``set_state`` checkpoint seam, so it works for
  any agent class). The baselines are this component's only state, checkpointed
  so a resumed run resets sellers to the same pristine text.
* **Eating** (every ``dial_setup`` step, all conditions). Upstream calls
  ``dial.get_eating_statement`` for every consumer at the end of every day,
  unconditionally — the ``asocial`` arm eats too, which is what keeps daily
  food demand and starvation live in the control condition. When the DIAL
  ceremony below is disabled the statement stays in the buyer's outcome queue
  and surfaces in the next morning's market observation, exactly upstream's
  queue mechanics.
* **The DIAL ceremony** (``dial_setup`` steps, gated by ``enabled``). Reproduces
  ``DayInTheLifeInitializer._process_dyad`` for each of today's dyads: generate
  the shared date scene from both partners' wearing statements, generate each
  partner's personal mundane events conditioned on that scene and on what they
  ate, then inject them as observations in upstream's order — a buyer's
  personal events first, then the shared setup, then the conversation opening.
  ``agent.observe`` is the port's equivalent of
  ``make_observation.add_to_queue``: the same text lands in the same memory.
  As upstream, only buyers paired today receive events; an odd buyer left
  without a date gets none.

The three experimental conditions differ only in this component's params:
``social`` runs everything, ``asocial_personal`` runs it with
``shared_setup: false`` (personal events only), ``asocial`` sets
``enabled: false`` — no generated content at all, but the seller reset and the
daily eating still run, as they do upstream.
"""

from __future__ import annotations

import copy
import logging
import random
from collections.abc import Mapping, Sequence
from typing import Any

from replications.signaling.components import dyads, prompts
from replications.signaling.components.calendar import (
    PHASE_DIAL_SETUP,
    PHASE_MARKET,
    DayCalendar,
)
from silisocs.environments.gm.components.base import UpdateComponent
from silisocs.runtime.telemetry.collector import SimMetricsCollector

logger = logging.getLogger(__name__)

#: Health counter for a generation call that failed; see ``_generate``.
GENERATION_FAILURE_COUNTER = "signaling_dial_generation_failures"


class DayBoundaryUpdateComponent(UpdateComponent):
    """Run the day-boundary duties: seller reset, eating, and the DIAL ceremony.

    Parameters
    ----------
    calendar
        The run's day layout (``calendar: ${calendar}``); it decides which steps
        are boundaries.
    personas_path
        Converted ``input/personas_la.json``; membership in it is what makes an
        agent a buyer, and it supplies the sexes the dyad schedule is built from.
    seed, num_days
        Feed the dyad schedule and the date-theme draw.
    enabled
        ``false`` skips the DIAL ceremony entirely (the ``asocial`` arm). The
        seller reset and the daily eating draw run regardless — upstream runs
        both in every condition.
    shared_setup, personal_events
        The condition switches (``asocial_personal`` sets ``shared_setup: false``).
    num_personal_events
        How many mundane events to request per buyer.
    """

    #: The ceremony must see every buyer and the seller reset every seller;
    #: ``BackendApp.update`` is population simulation — none of it may be
    #: narrowed by the participation filter.
    requires_full_roster = True

    def __init__(  # noqa: PLR0913 - every argument is a documented config knob
        self,
        *,
        context: Any = None,
        backend: Any = None,
        calendar: Mapping[str, Any] | None = None,
        personas_path: str = "",
        seed: int = 42,
        num_days: int = 5,
        enabled: bool = True,
        shared_setup: bool = True,
        personal_events: bool = True,
        num_personal_events: int = prompts.NUM_PERSONAL_EVENTS,
        **_ignored: Any,
    ) -> None:
        super().__init__()
        component = type(self).__name__
        backend = backend if backend is not None else getattr(context, "backend", None)
        if backend is None:
            raise ValueError(
                f"{component} needs the marketplace backend: the eating and wearing "
                "draws come from what each buyer owns."
            )
        self._backend = backend
        self._calendar = DayCalendar.from_config(calendar, component=component)
        self.personas_path = str(personas_path)
        self.seed = int(seed)
        self.num_days = int(num_days)
        self.enabled = bool(enabled)
        self.shared_setup = bool(shared_setup)
        self.personal_events = bool(personal_events)
        self.num_personal_events = int(num_personal_events)
        # The GM's model when it has one; otherwise resolved per call from the
        # acting agents, which always have one.
        self._model = getattr(context, "model", None)
        # Pristine per-seller agent state, snapshotted at step 0 and reapplied at
        # every day start (upstream's daily fresh-seller regeneration).
        self._seller_baselines: dict[str, Any] = {}

    # ---- the update slot --------------------------------------------------

    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        """Advance the backend, then run whichever boundary duties this step has.

        The backend update runs on EVERY step (this component owns the slot the
        built-in ``app_update`` would otherwise fill), so each finished market
        round resolves at the next step's update. By the market reflection —
        and a fortiori by the wearing draw here — the whole day's trading is
        final, exactly as upstream's engine resolves each round inside its own
        step before the reflection loop runs.
        """
        self._backend.update(
            step=step,
            agent_names=[str(agent.name) for agent in agents],
            context=context,
        )
        phase = self._calendar.phase_of(step)
        if phase.phase == PHASE_MARKET and phase.position == 0:
            self._reset_sellers(step=step, agents=agents)
        if phase.phase == PHASE_DIAL_SETUP:
            self._run_day_boundary(step=step, agents=agents)

    # ---- seller reset -----------------------------------------------------

    def _reset_sellers(self, *, step: int, agents: Sequence[Any]) -> None:
        """Restore every seller to its pristine state at each day start.

        At step 0 the sellers are already pristine, so the only work is
        snapshotting their agent state as the baseline; every later day start
        resets the backend ledgers (``restock_sellers``) and reapplies the
        baseline memory — upstream's amnesiac daily seller regeneration.
        """
        seller_names = set(getattr(self._backend, "seller_names", ()) or ())
        sellers = [agent for agent in agents if str(agent.name) in seller_names]
        if not self._seller_baselines:
            self._seller_baselines = {
                str(agent.name): copy.deepcopy(agent.get_state()) for agent in sellers
            }
            return
        if step == 0:
            return
        self._backend.restock_sellers()
        for agent in sellers:
            baseline = self._seller_baselines.get(str(agent.name))
            if baseline is not None:
                agent.set_state(copy.deepcopy(baseline))

    # ---- the dial_setup step ----------------------------------------------

    def _run_day_boundary(self, *, step: int, agents: Sequence[Any]) -> None:
        """Eat (all conditions), then run the DIAL ceremony when enabled."""
        agents_by_name = {str(agent.name): agent for agent in agents}
        day = int(self._calendar.day_of(step))
        buyers = [
            name
            for name in dyads.buyer_roster(list(agents_by_name), self.personas_path)
            if name in agents_by_name
        ]
        if not buyers:
            logger.warning("%s: no buyers present at step %s", type(self).__name__, step)
            return

        # Upstream eats unconditionally after the market reflection, in every
        # condition. With the ceremony disabled the statement is queued and
        # surfaces in the next market observation (upstream never pops it);
        # with the ceremony running it is handed to the events prompt instead
        # (upstream's append-then-pop).
        eating = {name: self._eating(name, queue_statement=not self.enabled) for name in buyers}
        if not self.enabled:
            return
        self._run_ceremony(day=day, buyers=buyers, agents_by_name=agents_by_name, eating=eating)

    def _run_ceremony(
        self,
        *,
        day: int,
        buyers: Sequence[str],
        agents_by_name: Mapping[str, Any],
        eating: Mapping[str, str],
    ) -> None:
        """Generate and inject the day's DIAL content for today's dyads."""
        pairs = [
            (first, second)
            for first, second in self._dyad_schedule(buyers).get(day, [])
            if first in agents_by_name and second in agents_by_name
        ]
        if not pairs:
            return
        model = self._resolve_model(agents_by_name)
        # Upstream's initializer runs with an empty components list, so the
        # prompts' {context} slot renders empty.
        context = ""

        # Wearing is drawn only for buyers actually on a date today, as
        # upstream draws it inside the per-dyad setup.
        wearing = {name: self._wearing(name) for pair in pairs for name in pair}

        scenes: dict[str, str] = {}
        if self.shared_setup and self._calendar.runs_dates:
            scenes = self._generate_scenes(
                model=model,
                day=day,
                pairs=pairs,
                agents_by_name=agents_by_name,
                wearing=wearing,
                context=context,
            )

        events: dict[str, list[str]] = {}
        if self.personal_events:
            events = {
                name: self._generate_events(
                    model=model,
                    agent=agents_by_name[name],
                    shared_event=scenes.get(name, ""),
                    eating=eating.get(name, ""),
                    context=context,
                )
                for pair in pairs
                for name in pair
            }

        # Upstream ordering (``_process_dyad``): a player's personal events are
        # queued first, then the shared setup, then the conversation opening.
        for pair in pairs:
            for name in pair:
                agent = agents_by_name[name]
                for number, event in enumerate(events.get(name, ()), start=1):
                    agent.observe(
                        prompts.PERSONAL_EVENT_OBSERVATION.format(number=number, event=event)
                    )
                scene = scenes.get(name, "")
                if scene:
                    agent.observe(prompts.SHARED_SETUP_OBSERVATION.format(event=scene))
                    agent.observe(prompts.CONVERSATION_OPENING)

    # ---- pieces -----------------------------------------------------------

    def _dyad_schedule(self, buyers: Sequence[str]) -> dyads.Schedule:
        """Return the run's dyad schedule (a pure function of roster, personas, seed)."""
        return dyads.build_schedule(
            list(buyers),
            num_days=self.num_days,
            personas_path=self.personas_path,
            seed=self.seed,
        )

    def _resolve_model(self, agents_by_name: Mapping[str, Any]) -> Any:
        """Return the model the generations run on: the GM's, else any agent's.

        A GM built without its own model still has agents bound to one, and the
        generation prompts are narrative text — the exact provider matters less
        than the run having one at all.
        """
        if self._model is not None:
            return self._model
        for agent in agents_by_name.values():
            agent_model = getattr(agent, "model", None)
            if agent_model is not None:
                return agent_model
        raise ValueError(
            f"{type(self).__name__}: no language model available on the game master or its agents."
        )

    def _eating(self, name: str, *, queue_statement: bool) -> str:
        try:
            return str(
                self._backend.consume_random_edible(name, queue_statement=queue_statement) or ""
            )
        except Exception as exc:
            logger.warning("%s: eating draw failed for %s: %s", type(self).__name__, name, exc)
            return ""

    def _wearing(self, name: str) -> str:
        try:
            return str(self._backend.wearing_statement(name) or "")
        except Exception as exc:
            logger.warning("%s: wearing draw failed for %s: %s", type(self).__name__, name, exc)
            return ""

    def _generate_scenes(  # noqa: PLR0913 - keyword-only, all of it one prompt's inputs
        self,
        *,
        model: Any,
        day: int,
        pairs: Sequence[tuple[str, str]],
        agents_by_name: Mapping[str, Any],
        wearing: Mapping[str, str],
        context: str,
    ) -> dict[str, str]:
        """One shared scene per dyad, keyed by *both* partners' names."""
        scenes: dict[str, str] = {}
        for first, second in pairs:
            prompt = prompts.SHARED_DIALOGUE_SETUP_PROMPT.format(
                context=context,
                player1=first,
                player2=second,
                player1_background=_background(agents_by_name[first]),
                player2_background=_background(agents_by_name[second]),
                player1_wearing_statement=wearing.get(first, ""),
                player2_wearing_statement=wearing.get(second, ""),
                date_theme=self._date_theme(day, first, second),
            )
            scene = _generate(
                model,
                prompt,
                what=f"shared setup ({first}, {second})",
                max_tokens=750,
                terminators=("\n",),
            )
            if scene:
                scenes[first] = scene
                scenes[second] = scene
        return scenes

    def _date_theme(self, day: int, first: str, second: str) -> str:
        """Draw this dyad's theme reproducibly.

        Upstream uses the global ``random``; here the draw is keyed by
        ``(seed, day, dyad)`` so it survives replay, resume, and concurrent
        scheduling. The seed is a *string*: seeding from a tuple would hash its
        strings, and string hashing is ``PYTHONHASHSEED``-salted per process.
        """
        rng = random.Random(f"{self.seed}|{day}|{first}|{second}")
        return rng.choice(prompts.DATE_THEMES)

    def _generate_events(
        self,
        *,
        model: Any,
        agent: Any,
        shared_event: str,
        eating: str,
        context: str,
    ) -> list[str]:
        prompt = prompts.PERSONAL_MUNDANE_EVENTS_PROMPT.format(
            context=context,
            player_name=agent.name,
            player_background=_background(agent),
            shared_event=shared_event,
            food_statement=eating,
            num_events=self.num_personal_events,
            delimiter=prompts.EVENT_DELIMITER,
        )
        raw = _generate(
            model,
            prompt,
            what=f"personal events ({agent.name})",
            max_tokens=1500,
            terminators=(),
        )
        events = [part.strip() for part in raw.split(prompts.EVENT_DELIMITER) if part.strip()]
        if events and len(events) != self.num_personal_events:
            logger.warning(
                "%s: generated %d events for %s, expected %d",
                type(self).__name__,
                len(events),
                agent.name,
                self.num_personal_events,
            )
        return events

    # ---- checkpoint contract ----------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Return the seller baselines — the component's only state."""
        return (
            {"seller_baselines": copy.deepcopy(self._seller_baselines)}
            if self._seller_baselines
            else {}
        )

    def set_state(self, state: Mapping[str, Any]) -> None:
        """Restore the pristine seller baselines from a checkpoint."""
        if state:
            self._seller_baselines = copy.deepcopy(dict(state.get("seller_baselines", {})))


def _background(agent: Any) -> str:
    """Render an agent's full identity + memory (upstream ``get_memories``).

    Upstream dumps the entity's entire memory bank — the ``[Persona]`` blob,
    formative memories, and everything the run has added since — and the
    initializer wraps it as ``Relevant Memories:``. The port stores the persona
    blob as the agent's *context* rather than a memory entry, so it must be
    prepended here explicitly or the generation prompts would see only the
    formative memories.
    """
    parts: list[str] = []
    persona = str(getattr(agent, "_persona_context", "") or "").strip()
    if persona:
        parts.append(persona)
    memories = getattr(agent, "get_all_memories_as_text", None)
    if callable(memories):
        text = "\n".join(str(item) for item in memories() or ()).strip()
        if text:
            parts.append(text)
    combined = "\n".join(parts).strip()
    if not combined:
        return "No background information available."
    return f"Relevant Memories:\n{combined}"


def _generate(
    model: Any,
    prompt: str,
    *,
    what: str,
    max_tokens: int = 1500,
    terminators: tuple[str, ...] = (),
) -> str:
    """One generation call; a failure skips that piece instead of aborting the day.

    The prompt is wrapped in upstream's ``interactive_document.open_question``
    envelope (``Question: ...\\nAnswer: ``), and callers pass upstream's
    per-call sampling limits: the shared scene stops at 750 tokens or the
    first newline; personal events run to 1500 tokens unterminated.

    Generation is serial here (upstream is too) and a day is ~75 calls at n=50,
    so a single transient provider error must not cost the whole run.
    """
    try:
        wrapped = f"Question: {prompt}\nAnswer: "
        return str(
            model.sample_text(wrapped, max_tokens=max_tokens, terminators=terminators) or ""
        ).strip()
    except Exception as exc:
        logger.warning("dial setup: %s generation failed: %s", what, exc)
        try:
            SimMetricsCollector.get().increment_counter(GENERATION_FAILURE_COUNTER)
        except Exception:  # pragma: no cover - telemetry must never break a run
            logger.debug("failed to record dial generation failure", exc_info=True)
        return ""
