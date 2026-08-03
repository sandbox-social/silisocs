"""The buyer agent: one persistent entity that runs the upstream question chains.

Upstream needs *two* agent prefabs for a buyer — ``agents/consumer.py`` for the
marketplace and ``agents/convo_agent.py`` for the date — plus a hand-copied
``__memory__`` state transfer between them (``dial.py``:216-227), because
Concordia rebuilds its whole simulation object graph per day and per dyad. That
split is a lifecycle workaround, not mechanism: the two prefabs differ only in
which *question chain* runs before the answer. SiliSocS agents persist for the
whole run, so the port needs one class and the memory-copying layer disappears
entirely.

What *is* mechanism is the chain itself. Both upstream prefabs are
``ConcatActComponent``s over a fixed list of ``QuestionOfRecentMemories``
components: before answering, the agent asks itself a fixed sequence of
questions and the answers — each under its component's ``pre_act_label`` — are
concatenated into the context the acting call finally sees. This class
reproduces that: one ``sample_text`` per question, answers concatenated under
:data:`prompts.QUESTION_LABELS`, then the real action call.

Which chain runs is decided by the **offered action surface**, not by config:
a spec offering ``BID``/``ASK`` is a marketplace turn (consumer chain), one
offering ``SEND_MESSAGE`` is a conversational turn (convo chain), and anything
else — the free-text journal reflections — takes the plain ``NativeAgent`` path,
exactly as upstream's ``SwitchingActComponent`` switches on the action spec.
This keeps one agent class usable across all three game masters with no
per-phase wiring.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from replications.signaling.components import prompts
from silisocs.agents.native import NativeAgent
from silisocs.runtime.telemetry.collector import SimMetricsCollector
from silisocs.runtime.types import ActionOutput, ActionSpec

logger = logging.getLogger(__name__)

#: (label key, question template) for a marketplace turn — agents/consumer.py.
CONSUMER_CHAIN: tuple[tuple[str, str], ...] = (
    ("situation", prompts.SITUATION_PERCEPTION_QUESTION),
    ("self", prompts.CONSUMER_SELF_PERCEPTION_QUESTION),
    ("evaluation", prompts.CONSUMER_EVALUATION_QUESTION),
)

#: (label key, question template) for a conversational turn — agents/convo_agent.py.
CONVO_CHAIN: tuple[tuple[str, str], ...] = (
    ("situation", prompts.CONVO_SITUATION_QUESTION),
    ("self", prompts.CONVO_SELF_PERCEPTION_QUESTION),
    ("person_by_situation", prompts.PERSON_BY_SITUATION_QUESTION),
    ("last_sentence", prompts.LAST_SENTENCE_QUESTION),
    ("strategy", prompts.PINK_NOISE_QUESTION),
)

#: Tool names that identify a marketplace turn / a conversational turn.
_MARKET_TOOLS = frozenset({"BID", "ASK"})
_CONVO_TOOLS = frozenset({"SEND_MESSAGE"})

#: Health counter for a question call that failed. Registered here rather than in
#: the core vocabulary because it is replication-local; it still surfaces in
#: ``sim_metrics.json`` via the collector.
QUESTION_FAILURE_COUNTER = "signaling_question_failures"


class SignalingAgent(NativeAgent):
    """A buyer that runs upstream's pre-act question chain before every action.

    Parameters
    ----------
    question_chain
        Whether to run the chains at all. ``False`` collapses the agent to plain
        :class:`~silisocs.agents.native.NativeAgent` behaviour, which is what
        scripted/deterministic runs and tests want: the chain costs 3-5 extra
        model calls per turn and contributes nothing when the model is a stub.
    **kwargs
        Passed through verbatim to :class:`NativeAgent` (``name``, ``model``,
        persona/goal text, memory sizing, ...).
    """

    def __init__(self, *, question_chain: bool = True, **kwargs: Any) -> None:
        # Upstream injects GOAL_TEXT as a Constant component on every buyer
        # (simulation.get_marketplace_config). Defaulting it here rather than in
        # YAML keeps the verbatim wording in exactly one place (prompts.py); a
        # scenario that sets an explicit goal still wins.
        if not str(kwargs.get("goal") or "").strip():
            kwargs["goal"] = prompts.GOAL_TEXT.format(agent_name=str(kwargs.get("name", "")))
        super().__init__(**kwargs)
        self.question_chain = bool(question_chain)

    # ---- pipeline selection --------------------------------------------

    def _questions_for(self, action_spec: ActionSpec) -> tuple[tuple[str, str], ...]:
        """Pick the chain for one action spec from the actions it offers."""
        if not self.question_chain:
            return ()
        offered = _offered_tool_names(action_spec)
        if offered & _MARKET_TOOLS:
            return CONSUMER_CHAIN
        if offered & _CONVO_TOOLS:
            return CONVO_CHAIN
        return ()

    # ---- chain execution -------------------------------------------------

    def _question_prompt(self, base_context: str, template: str) -> str:
        return f"{base_context}\n\n{template.format(name=self.name)}".strip()

    def _label(self, key: str) -> str:
        return prompts.QUESTION_LABELS[key].format(name=self.name)

    def _record_question_failure(self, key: str, exc: BaseException) -> None:
        """Count a failed question instead of letting it abort the turn.

        One transient provider error should degrade this agent's context for one
        turn, not kill the run: the acting call still happens, with whatever
        answers were gathered before the failure.
        """
        logger.warning("signaling question '%s' failed for %s: %s", key, self.name, exc)
        try:
            SimMetricsCollector.get().increment_counter(QUESTION_FAILURE_COUNTER)
        except Exception:  # pragma: no cover - telemetry must never break a turn
            logger.debug("failed to record signaling question failure", exc_info=True)

    def _run_chain(self, base_context: str, action_spec: ActionSpec) -> list[tuple[str, str]]:
        answers: list[tuple[str, str]] = []
        for key, template in self._questions_for(action_spec):
            try:
                answer = self.model.sample_text(self._question_prompt(base_context, template))
            except Exception as exc:
                self._record_question_failure(key, exc)
                continue
            if str(answer or "").strip():
                answers.append((self._label(key), str(answer).strip()))
        return answers

    async def _run_chain_async(
        self, base_context: str, action_spec: ActionSpec
    ) -> list[tuple[str, str]]:
        answers: list[tuple[str, str]] = []
        for key, template in self._questions_for(action_spec):
            prompt = self._question_prompt(base_context, template)
            try:
                answer = await _sample_text_async(self.model, prompt)
            except Exception as exc:
                self._record_question_failure(key, exc)
                continue
            if str(answer or "").strip():
                answers.append((self._label(key), str(answer).strip()))
        return answers

    @staticmethod
    def _compose(base_context: str, answers: list[tuple[str, str]]) -> str:
        """Concatenate the chain answers onto the base context.

        Mirrors upstream's ``ConcatActComponent``: each component's contribution
        appears under its ``pre_act_label``, in chain order, after the agent's
        standing context.
        """
        if not answers:
            return base_context
        blocks = [f"{label}:\n{answer}" for label, answer in answers]
        return "\n\n".join([base_context, *blocks]) if base_context else "\n\n".join(blocks)

    # ---- acting ----------------------------------------------------------

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        """Run the spec's question chain, then answer with the enriched context."""
        base_context = self._context()
        answers = self._run_chain(base_context, action_spec)
        context = self._compose(base_context, answers)
        result = self._call_model(context, action_spec)
        return self._finish(context, action_spec, result, answers)

    async def act_async(self, action_spec: ActionSpec) -> ActionOutput:
        """Loop-native twin of :meth:`act`.

        The chain is 3-5 extra model calls per turn on top of the action call,
        and ~25-50 agents act concurrently per step, so holding a thread for each
        of those calls would defeat the asyncio executor entirely.
        """
        base_context = self._context()
        answers = await self._run_chain_async(base_context, action_spec)
        context = self._compose(base_context, answers)
        result = await self._call_model_async(context, action_spec)
        return self._finish(context, action_spec, result, answers)

    def _finish(
        self,
        context: str,
        action_spec: ActionSpec,
        result: ActionOutput,
        answers: list[tuple[str, str]],
    ) -> ActionOutput:
        """Record the turn, adding the chain answers to ``prompts_and_responses``."""
        output = self._record_act(context, action_spec, result)
        if answers:
            self._last_log["chain"] = [
                {"label": label, "answer": answer} for label, answer in answers
            ]
        return output


def _offered_tool_names(action_spec: ActionSpec) -> frozenset[str]:
    """Names of the backend actions an action spec offers, upper-cased.

    Tool schemas arrive as OpenAI function entries
    (``{"type": "function", "function": {"name": ...}}``); a spec that offers no
    tools (the free-text journal steps) yields the empty set.
    """
    tools = getattr(action_spec, "extra_args", {}).get("tools")
    if not isinstance(tools, (list, tuple)):
        return frozenset()
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        name = function.get("name") if isinstance(function, dict) else tool.get("name")
        if name:
            names.add(str(name).strip().upper())
    return frozenset(names)


async def _sample_text_async(model: Any, prompt: str) -> str:
    """Await the model's async text sampler, falling back to a helper thread.

    Same contract as ``Agent._call_model_async``: a duck-typed model with only a
    sync ``sample_text`` still works under the asyncio executor.
    """
    async_fn = getattr(model, "sample_text_async", None)
    if callable(async_fn):
        return await async_fn(prompt)
    return await asyncio.to_thread(model.sample_text, prompt)
