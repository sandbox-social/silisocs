"""Typed probe definitions for structured questionnaire probes.

Each probe type handles its own prompt generation and answer parsing.
New probes can be created from YAML config without writing Python code.

Supported types
---------------
- ``NumericRatingProbe``  – expects a number in a user-defined range
- ``BinaryProbe``         – expects yes/no
- ``ChoiceProbe``         – expects one of a fixed set of choices
- ``FreeTextProbe``       – accepts any text answer
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from concordia.typing import entity


def _get_agent_name(agent: Any) -> str:
    """Extract agent name, preferring ``_agent_name`` over ``name``."""
    name = getattr(agent, "_agent_name", None)
    if name is not None:
        return name
    return getattr(agent, "name", "unknown")


class ProbeBase(ABC):
    """Minimal abstract base for all probe types.

    Subclasses must set ``name`` and implement ``prompt_text``, ``parse_answer``,
    and ``form_question_for_agent``.  Everything else (ask, submit, questionnaire
    integration) is provided.
    """

    name: str

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------
    @abstractmethod
    def form_question_for_agent(self, agent: Any) -> str:
        """Return the question text with agent-specific placeholders resolved."""

    def _make_action_spec(self, prompt: str) -> entity.ActionSpec:
        try:
            return entity.ActionSpec(
                call_to_action=prompt,
                output_type=entity.OutputType.FREE,
                tag="query",
            )
        except TypeError:
            return entity.ActionSpec(
                call_to_action=prompt,
                output_type=entity.OutputType.FREE,
            )

    _CALL_TO_SPEECH = (
        "Given the above, what is {name} likely to say next? Respond in"
        ' the format `{name} -- "..."` For example, '
        'Cristina -- "Hello! Mighty fine weather today, right?", '
        'Ichabod -- "I wonder if the alfalfa is ready to harvest", or '
        'Townsfolk -- "Good morning".\n'
    )

    def form_query_for_agent(self, agent: Any) -> str:
        agent_name = _get_agent_name(agent)
        question = self.form_question_for_agent(agent)
        return "Context: " + question + self._CALL_TO_SPEECH.format(name=agent_name)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def ask(self, agent: Any) -> str:
        prompt = self.form_query_for_agent(agent)
        return agent.act(action_spec=self._make_action_spec(prompt))

    @abstractmethod
    def parse_answer(self, raw: str) -> str | None:
        """Extract the structured answer from raw LLM text."""

    def submit(self, agent: Any) -> dict[str, Any]:
        return self.submit_with_raw_response(self.ask(agent))

    def submit_with_raw_response(self, raw: str) -> dict[str, Any]:
        return {
            "query_type": self.name,
            "raw_response": raw,
            "query_return": self.parse_answer(raw or ""),
        }


# ======================================================================
# Concrete probe types
# ======================================================================

class NumericRatingProbe(ProbeBase):
    """Probe that asks for an integer in ``[lo, hi]``.

    YAML example::

        query_type: NumericRatingProbe
        query_data:
          name: Favorability
          question: "Rate your opinion of {candidate} from {lo} to {hi}."
          lo: 1
          hi: 10
          context: "{agentname} rates election candidate {candidate}."
          labels:
            candidate: Bill Fredrickson
    """

    def __init__(self, query_data: dict[str, Any] | None = None):
        cfg = query_data or {}
        self.name = cfg.get("name", "NumericRating")
        self._question = cfg.get("question", "Return a single numeric value from {lo} to {hi}.")
        self._context = cfg.get("context", "")
        self._lo = int(cfg.get("lo", 1))
        self._hi = int(cfg.get("hi", 10))
        self._labels: dict[str, str] = dict(cfg.get("labels", {}))

    def form_question_for_agent(self, agent: Any) -> str:
        agent_name = _get_agent_name(agent)
        subs = {**self._labels, "agentname": agent_name, "lo": self._lo, "hi": self._hi}
        parts = []
        if self._context:
            parts.append(self._context.format(**subs))
        parts.append(self._question.format(**subs))
        return " ".join(parts)

    def parse_answer(self, raw: str) -> str | None:
        # Match any integer, then validate range.
        for m in re.finditer(r"\b(\d+)\b", raw):
            val = int(m.group(1))
            if self._lo <= val <= self._hi:
                return str(val)
        return None


class BinaryProbe(ProbeBase):
    """Probe that asks a yes/no question.

    YAML example::

        query_type: BinaryProbe
        query_data:
          name: VoteIntent
          question: "Will you cast a vote? Reply yes or no."
          context: "{agentname} is asked about voting."
    """

    def __init__(self, query_data: dict[str, Any] | None = None):
        cfg = query_data or {}
        self.name = cfg.get("name", "Binary")
        self._question = cfg.get("question", "Reply yes or no.")
        self._context = cfg.get("context", "")
        self._labels: dict[str, str] = dict(cfg.get("labels", {}))

    def form_question_for_agent(self, agent: Any) -> str:
        agent_name = _get_agent_name(agent)
        subs = {**self._labels, "agentname": agent_name}
        parts = []
        if self._context:
            parts.append(self._context.format(**subs))
        parts.append(self._question.format(**subs))
        return " ".join(parts)

    def parse_answer(self, raw: str) -> str | None:
        lower = raw.lower()
        if "yes" in lower:
            return "Yes"
        if "no" in lower:
            return "No"
        return None


class ChoiceProbe(ProbeBase):
    """Probe that asks the agent to pick from a fixed set of choices.

    YAML example::

        query_type: ChoiceProbe
        query_data:
          name: VotePref
          question: "Name the candidate you want to vote for."
          context: "{agentname} is voting for either {candidate1} or {candidate2}."
          choices:
            - Bill Fredrickson
            - Bradley Carter
          labels:
            candidate1: Bill Fredrickson
            candidate2: Bradley Carter
    """

    def __init__(self, query_data: dict[str, Any] | None = None):
        cfg = query_data or {}
        self.name = cfg.get("name", "Choice")
        self._question = cfg.get("question", "Pick one of the choices.")
        self._context = cfg.get("context", "")
        self._choices: list[str] = list(cfg.get("choices", []))
        self._labels: dict[str, str] = dict(cfg.get("labels", {}))

    def form_question_for_agent(self, agent: Any) -> str:
        agent_name = _get_agent_name(agent)
        subs = {**self._labels, "agentname": agent_name}
        parts = []
        if self._context:
            parts.append(self._context.format(**subs))
        parts.append(self._question.format(**subs))
        return " ".join(parts)

    def parse_answer(self, raw: str) -> str | None:
        lower = raw.lower()
        # Check each choice's full name and individual tokens.
        for choice in self._choices:
            if choice.lower() in lower:
                return choice
            for token in choice.split():
                if token.lower() in lower:
                    return choice
        return None


class FreeTextProbe(ProbeBase):
    """Probe that accepts arbitrary text.

    YAML example::

        query_type: FreeTextProbe
        query_data:
          name: OpenThoughts
          question: "What are your thoughts on the election?"
          context: "{agentname} reflects on the upcoming election."
    """

    def __init__(self, query_data: dict[str, Any] | None = None):
        cfg = query_data or {}
        self.name = cfg.get("name", "FreeText")
        self._question = cfg.get("question", "Share your thoughts.")
        self._context = cfg.get("context", "")
        self._labels: dict[str, str] = dict(cfg.get("labels", {}))

    def form_question_for_agent(self, agent: Any) -> str:
        agent_name = _get_agent_name(agent)
        subs = {**self._labels, "agentname": agent_name}
        parts = []
        if self._context:
            parts.append(self._context.format(**subs))
        parts.append(self._question.format(**subs))
        return " ".join(parts)

    def parse_answer(self, raw: str) -> str | None:
        text = raw.strip() if raw else None
        return text or None


class TemplateProbe(ProbeBase):
    """Probe built from a ``query_text`` template dict (legacy format).

    This preserves the template-composition style used by the original
    ``AgentQuery`` base class while unifying it under :class:`ProbeBase`.
    Subclasses define ``name`` and ``query_text`` as class attributes, then
    pass ``query_data`` at construction time to fill template placeholders.

    ``query_text`` is a dict of named sections, each with a ``"text"`` key
    (a format string) and optionally ``"static_labels"`` listing expected
    keys in ``query_data``.
    """

    name: str = "TemplateProbe"
    query_text: dict[str, dict[str, Any]] = {}

    def __init__(self, query_data: dict[str, Any] | None = None):
        self.query_data = query_data or {}
        self.question_template = ""
        for component_name, component in self.query_text.items():
            if "static_labels" in component:
                premise_actors = [
                    actor
                    for actor in self.query_data[component_name]
                    if self.query_data[component_name][actor] is not None
                ]
                assert component["static_labels"] == premise_actors, (
                    "query data doesn't match query"
                )
                self.question_template += component["text"].format(
                    **self.query_data[component_name]
                )
            else:
                self.question_template += component["text"]

    def form_question_for_agent(self, agent: Any) -> str:
        agent_name = _get_agent_name(agent)
        return self.question_template.format(agentname=agent_name)

    @abstractmethod
    def parse_answer(self, raw: str) -> str | None:
        """Subclasses must extract the structured answer."""

    def submit_with_raw_response(self, raw: str) -> dict[str, Any]:
        query_return = dict(self.query_data) if self.query_data else {}
        query_return.setdefault("query_type", self.name)
        query_return["raw_response"] = raw
        query_return["query_return"] = self.parse_answer(raw or "")
        return query_return


# Backward-compatible alias for the original AgentQuery base class.
AgentQuery = TemplateProbe


# Registry for YAML-driven instantiation.
PROBE_TYPES: dict[str, type[ProbeBase]] = {
    "NumericRatingProbe": NumericRatingProbe,
    "BinaryProbe": BinaryProbe,
    "ChoiceProbe": ChoiceProbe,
    "FreeTextProbe": FreeTextProbe,
    "TemplateProbe": TemplateProbe,
}
