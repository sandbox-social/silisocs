"""Typed probe definitions for structured questionnaire probes.

Each probe type handles its own prompt generation and answer parsing.
New probes can be created from YAML config without writing Python code.

Supported types
---------------
- ``NumericRatingProbe``  -- expects a number in a user-defined range
- ``BinaryProbe``         -- expects yes/no
- ``ChoiceProbe``         -- expects one of a fixed set of choices
- ``StructuredProbe``     -- expects a JSON object matching a schema
- ``FreeTextProbe``       -- accepts any text answer
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from silisocs.runtime.types import ActionSpec, OutputType


def _get_agent_name(agent: Any) -> str:
    """Extract agent name, preferring ``_agent_name`` over ``name``."""
    name = getattr(agent, "_agent_name", None)
    if name is not None:
        return name
    return getattr(agent, "name", "unknown")


class ProbeBase(ABC):
    """Abstract base for all probe types.

    Subclasses must set ``name`` and implement ``form_question_for_agent`` and
    ``parse_answer``.  Everything else (prompt formatting, ask, submit) is
    provided by the base class.
    """

    name: str

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------
    @abstractmethod
    def form_question_for_agent(self, agent: Any) -> str:
        """Return the question text with agent-specific placeholders resolved."""

    def _make_action_spec(self, prompt: str) -> ActionSpec:
        return ActionSpec(
            prompt=prompt,
            output_type=OutputType.TEXT,
            tag="probe",
        )

    def form_probe_for_agent(self, agent: Any) -> str:
        """Build the full prompt sent to the model for this probe.

        Wraps the question in a short instruction frame that asks the model
        to answer directly.  This replaces the legacy completion-style prompt
        that assumed the model would continue a dialogue transcript.
        """
        question = self.form_question_for_agent(agent)
        return f"{question}\n\nAnswer the question above directly and concisely."

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def ask(self, agent: Any) -> str:
        """Send the probe to the agent and return the raw response text."""
        prompt = self.form_probe_for_agent(agent)
        return str(agent.act(action_spec=self._make_action_spec(prompt)))

    @abstractmethod
    def parse_answer(self, raw: str) -> str | None:
        """Extract the structured answer from raw LLM text."""

    def submit(self, agent: Any) -> dict[str, Any]:
        """Ask the agent and return a structured probe result dict."""
        return self.submit_with_raw_response(self.ask(agent))

    def submit_with_raw_response(self, raw: str) -> dict[str, Any]:
        """Parse a raw response and return a structured probe result dict."""
        return {
            "probe_type": self.name,
            "raw_response": raw,
            "probe_return": self.parse_answer(raw or ""),
        }


# ======================================================================
# Concrete probe types
# ======================================================================


class NumericRatingProbe(ProbeBase):
    """Probe that asks for an integer in ``[lo, hi]``.

    YAML example::

        probe_type: NumericRatingProbe
        probe_data:
          name: Favorability
          question: "Rate your opinion of {candidate} from {lo} to {hi}."
          lo: 1
          hi: 10
          context: "{agentname} rates election candidate {candidate}."
          labels:
            candidate: Candidate A
    """

    def __init__(self, probe_data: dict[str, Any] | None = None):
        cfg = probe_data or {}
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
        for m in re.finditer(r"\b(\d+)\b", raw):
            val = int(m.group(1))
            if self._lo <= val <= self._hi:
                return str(val)
        return None


class BinaryProbe(ProbeBase):
    """Probe that asks a yes/no question.

    YAML example::

        probe_type: BinaryProbe
        probe_data:
          name: VoteIntent
          question: "Will you cast a vote? Reply yes or no."
          context: "{agentname} is asked about voting."
    """

    def __init__(self, probe_data: dict[str, Any] | None = None):
        cfg = probe_data or {}
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

        probe_type: ChoiceProbe
        probe_data:
          name: VotePref
          question: "Name the candidate you want to vote for."
          context: "{agentname} is voting for either {choice1} or {choice2}."
          choices:
            - Candidate A
            - Candidate B
          labels:
            choice1: Candidate A
            choice2: Candidate B
    """

    def __init__(self, probe_data: dict[str, Any] | None = None):
        cfg = probe_data or {}
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

        probe_type: FreeTextProbe
        probe_data:
          name: OpenThoughts
          question: "What are your thoughts on the election?"
          context: "{agentname} reflects on the upcoming election."
    """

    def __init__(self, probe_data: dict[str, Any] | None = None):
        cfg = probe_data or {}
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


class StructuredProbe(ProbeBase):
    """Probe that asks for one JSON object through structured-response mode."""

    def __init__(self, probe_data: dict[str, Any] | None = None):
        cfg = probe_data or {}
        self.name = cfg.get("name", "Structured")
        self._question = cfg.get("question", "Return a JSON object.")
        self._context = cfg.get("context", "")
        self._labels: dict[str, str] = dict(cfg.get("labels", {}))
        self._schema: dict[str, Any] = dict(
            cfg.get("schema", {"type": "object", "additionalProperties": True})
        )

    def _make_action_spec(self, prompt: str) -> ActionSpec:
        try:
            return ActionSpec(
                prompt=prompt,
                output_type=OutputType.STRUCTURED,
                tag="structured_probe",
                extra_args={"schema": self._schema},
            )
        except TypeError:
            return ActionSpec(
                prompt=prompt,
                output_type=OutputType.STRUCTURED,
                extra_args={"schema": self._schema},
            )

    def form_question_for_agent(self, agent: Any) -> str:
        agent_name = _get_agent_name(agent)
        subs = {**self._labels, "agentname": agent_name}
        parts = []
        if self._context:
            parts.append(self._context.format(**subs))
        parts.append(self._question.format(**subs))
        return " ".join(parts)

    def parse_answer(self, raw: Any) -> dict[str, Any] | None:  # type: ignore[override]
        if isinstance(raw, dict):
            return dict(raw)
        raise TypeError("StructuredProbe expects a typed dict response.")


# Registry for YAML-driven instantiation.
PROBE_TYPES: dict[str, type[ProbeBase]] = {
    "NumericRatingProbe": NumericRatingProbe,
    "BinaryProbe": BinaryProbe,
    "ChoiceProbe": ChoiceProbe,
    "StructuredProbe": StructuredProbe,
    "FreeTextProbe": FreeTextProbe,
}
