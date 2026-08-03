"""Deterministic scripted behaviour for LLM-free runs of the replication.

Plugged into the framework's ``scripted`` provider
(``sim.llm.provider: scripted`` + ``extra_kwargs.behavior_class_path``), this
makes every phase of the experiment executable with no model calls at all: the
integration tests drive all three conditions end to end this way, and it is the
cheapest way to check a config change actually composes and runs.

It answers the way a *competent* participant would — a buyer bids on a good it
can afford, a seller asks above its cost, a date sends a line to the right
partner, a reflection mentions a rating — because the point is to exercise the
mechanism (orders clear, ratings parse, dyads alternate), not to model anyone.

Determinism comes from the turn context the engine stamps on the model
(``agent_name``, ``episode_idx``) plus the same dyad schedule the backends
derive, so a scripted run is byte-identical across replays and resumes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from replications.signaling.components import dyads, goods
from replications.signaling.components import prompts as prompts_module
from replications.signaling.components.calendar import (
    DATE_REFLECTION_KINDS,
    PHASE_DATE,
    DayCalendar,
)

_EVENT_COUNT = re.compile(r"Generate exactly (\d+) mundane")


def _requested_event_count(prompt: str) -> int:
    """How many personal events the handler asked for (upstream default 5)."""
    match = _EVENT_COUNT.search(prompt)
    return int(match.group(1)) if match else prompts_module.NUM_PERSONAL_EVENTS


class ScriptedSignalingBehavior:
    """Answers every prompt the replication issues, without a language model."""

    def __init__(  # noqa: PLR0913 - mirrors the scenario config block it is built from
        self,
        *,
        goods_path: str = "",
        sellers_path: str = "",
        personas_path: str = "",
        num_agents: int = 10,
        num_days: int = 5,
        seed: int = 42,
        calendar: Mapping[str, Any] | None = None,
    ) -> None:
        self._goods_table = goods.load_goods_table(goods_path) if goods_path else {}
        self._goods = goods.flatten_goods(self._goods_table)
        self._by_id = {good.id: good for good in self._goods}
        self._seller_goods: dict[str, dict[str, Any]] = {}
        if sellers_path and Path(sellers_path).is_file():
            with open(sellers_path, encoding="utf-8") as handle:
                self._seller_goods = {str(r["name"]): r for r in json.load(handle)}
        self._personas_path = str(personas_path)
        self._seed = int(seed)
        self._num_days = int(num_days)
        self._calendar = DayCalendar.from_config(calendar, component=type(self).__name__)
        self._buyers: list[str] = []
        if self._personas_path and Path(self._personas_path).is_file():
            records = dyads.load_persona_records(self._personas_path)
            self._buyers = [str(r["name"]) for r in records][: int(num_agents)]
        self._schedule = (
            dyads.build_schedule(
                self._buyers,
                num_days=self._num_days,
                personas_path=self._personas_path,
                seed=self._seed,
            )
            if self._buyers and self._calendar.runs_dates
            else {}
        )

    # ---- turn context ---------------------------------------------------

    @staticmethod
    def _context(model: Any) -> tuple[str, int]:
        """Return the acting agent and episode the engine stamped on the model."""
        local = getattr(model, "_local", None)
        name = str(getattr(local, "agent_name", "") or "")
        step = int(getattr(local, "episode_idx", 0) or 0)
        return name, step

    def _pick(self, *parts: Any) -> int:
        """Return a stable pseudo-random integer derived from the given parts.

        Not ``hash``: it is salted per process, which would break replay.
        """
        digest = hashlib.sha256("|".join(str(p) for p in (self._seed, *parts)).encode()).digest()
        return int.from_bytes(digest[:8], "big")

    # ---- model interface ------------------------------------------------

    def sample_tool_calls(
        self, prompt: str, tools: Sequence[Mapping[str, Any]], *, model: Any = None
    ) -> list[dict[str, Any]]:
        """Return one valid call for whichever surface the GM offered."""
        names = {str(t.get("function", {}).get("name", "")) for t in tools or ()}
        agent_name, step = self._context(model)
        if "ASK" in names and agent_name in self._seller_goods:
            record = self._seller_goods[agent_name]
            return [
                {
                    "name": "ASK",
                    "arguments": {
                        "good": str(record["good"]),
                        # Above cost, so the ask is profitable and can cross a bid.
                        "price": round(float(record["cost"]) * 1.2, 2),
                        "qty": 1,
                    },
                }
            ]
        if "BID" in names:
            good = self._bid_target(agent_name, step, prompt)
            return [
                {
                    "name": "BID",
                    "arguments": {
                        "good": good.id,
                        # Above the listed price so bids and asks actually cross.
                        "price": round(float(good.price or 1.0) * 1.5, 2),
                        "qty": 1,
                    },
                }
            ]
        if "SEND_MESSAGE" in names:
            return [
                {
                    "name": "SEND_MESSAGE",
                    "arguments": {
                        "recipient": self._partner(agent_name, step, prompt),
                        "text": f"[scripted] {agent_name} keeps the conversation going.",
                    },
                }
            ]
        return []

    def sample_text(self, prompt: str, *, model: Any = None) -> str:
        """Answer a free-text turn: DIAL generation, reflections, question chains."""
        agent_name, step = self._context(model)
        # The day-boundary handler's two generation prompts are issued outside a
        # turn, so there is no agent/episode context to switch on; recognise them
        # by their instructions. Answering them properly is what makes a scripted
        # run exercise the DIAL path rather than skip it.
        if "Separate each event with the delimiter" in prompt:
            count = _requested_event_count(prompt)
            return f" {prompts_module.EVENT_DELIMITER} ".join(
                f"[scripted] Ordinary event {index + 1} of the day." for index in range(count)
            )
        if "Generate a shared scenario" in prompt:
            return "[scripted] They meet for a first date at a quiet corner table."

        kind = self._calendar.reflection_kind(step)
        if kind == "rating":
            # A parseable rating: the journal backend extracts the first number.
            value = 1.0 + (self._pick(agent_name, step) % 90) / 10.0
            return f"[scripted] I would rate them {value:.1f} out of 10."
        if kind in DATE_REFLECTION_KINDS:
            return f"[scripted] {agent_name}'s {kind.replace('_', ' ')} for step {step}."
        if kind:
            return f"[scripted] {agent_name} reflects on the day's purchases."
        # The agent's internal question chain (situation/self/strategy...).
        return f"[scripted] {agent_name} considers the situation at step {step}."

    # ---- helpers --------------------------------------------------------

    def _bid_target(self, agent_name: str, step: int, prompt: str) -> goods.Good:
        """Prefer food (so nobody starves), otherwise rotate over the catalogue."""
        del prompt
        edibles = [self._by_id[i] for i in goods.edible_ids(self._goods_table) if i in self._by_id]
        pool = edibles if edibles and step % 2 == 0 else self._goods
        if not pool:
            return goods.Good(category="", quality="", id="", price=1.0)
        return pool[self._pick(agent_name, step) % len(pool)]

    def _partner(self, agent_name: str, step: int, prompt: str) -> str:
        """Today's date, from the same schedule the backends derive."""
        phase = self._calendar.phase_of(step)
        if phase.phase == PHASE_DATE:
            partners = dyads.partners_for_day(self._schedule, phase.day)
            if agent_name in partners:
                return partners[agent_name]
        # Fall back to whatever the observation named, so a roster mismatch
        # produces a rejected action rather than a crash.
        match = re.search(r"You are on a first date with ([^.\n]+)", prompt)
        return match.group(1).strip() if match else ""
