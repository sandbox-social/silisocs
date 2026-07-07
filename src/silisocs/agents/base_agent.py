"""Agent base classes and interfaces.

This module declares the abstract :class:`silisocs.agents.base_agent.Agent` interface used by the
simulation runtime as well as helper classes and lightweight concrete
implementations used by the project.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable

    from silisocs.runtime.language_models import LanguageModel
    from silisocs.runtime.types import ActionOutput, ActionSpec


class _ModelCallPlan(NamedTuple):
    """One planned model call: which sampling method to invoke and how to wrap
    its result into an :class:`ActionOutput`. Shared by the sync and async
    ``_call_model`` executors so the output-type dispatch exists exactly once.
    """

    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    finish: Callable[[Any], ActionOutput]


class Agent(ABC):
    """Abstract base class for social-media simulation agents.

    All agents, whether component-based or custom implementations, must
    implement these core methods.
    """

    def __init__(self, model: LanguageModel) -> None:
        """Store the language model used by this agent."""
        self._model = model

    @property
    def model(self) -> LanguageModel:
        """Return the language model bound to this agent."""
        return self._model

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the agent's display name.

        Returns
        -------
        str
            The agent's name used in logs and output.
        """

    @abstractmethod
    def observe(self, observation: str) -> None:
        """Process an observation from the environment.

        Called at the start of each action cycle to inform the agent about the
        current state of the social-media timeline or environment.

        Parameters
        ----------
        observation : str
            A text observation (timeline, episode number, etc.)
        """

    def initialize(self, context: Any | None = None) -> None:
        """Initialize the agent before the simulation loop starts.

        Runtime initializers may pass shared memories, specific memories, or
        other setup context here. Simple custom agents can ignore it.
        """
        del context

    def get_state(self) -> dict[str, Any]:
        """Return serializable agent state for checkpoints."""
        return {}

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore agent state from a checkpoint payload."""
        del state

    @abstractmethod
    def act(self, action_spec: Any) -> ActionOutput:
        """Generate the next action based on the current action specification.

        Called to request an action from the agent. The action_spec encodes the
        current context and desired behavior for this action cycle.

        Parameters
        ----------
        action_spec : Any
            ActionSpec object providing context, prompts, and constraints.

        Returns
        -------
        ActionOutput
            A typed action response for the engine and game master to resolve.
        """

    async def act_async(self, action_spec: Any) -> ActionOutput:
        """Async twin of :meth:`act`, used by the asyncio turn executor.

        The default runs the sync ``act`` on a helper thread, so an agent that
        only implements ``act`` is blocking-safe under the asyncio executor and
        mixes freely with async-native agents in the same step. Override this
        (typically routing through :meth:`_call_model_async`) to act on the
        event loop itself, where thousands of turns can be in flight at once.
        Sync ``act`` remains the required contract; this override is optional.
        """
        return await asyncio.to_thread(self.act, action_spec)

    def _plan_model_call(
        self, context: str, action_spec: ActionSpec
    ) -> ActionOutput | _ModelCallPlan:
        """Map one curated context + action spec to the model call it requires.

        Returns either a finished :class:`ActionOutput` (no model call needed)
        or a :class:`_ModelCallPlan` naming the sampling method, its arguments,
        and the result-to-ActionOutput wrapper. ``_call_model`` executes the
        plan synchronously and ``_call_model_async`` awaits the method's
        ``*_async`` twin, so both paths share this single dispatch.
        """
        from silisocs.runtime.types import (
            CHOICE_ACTION_TYPES,
            FREE_ACTION_TYPES,
            ActionOutput,
            OutputType,
        )

        prompt = _join_context_and_prompt(context, action_spec.prompt)
        model_kwargs = dict(action_spec.extra_args.get("model_kwargs") or {})

        if action_spec.output_type == OutputType.SKIP:
            return ActionOutput.skip()

        if action_spec.output_type in FREE_ACTION_TYPES:
            return _ModelCallPlan("sample_text", (prompt,), model_kwargs, ActionOutput.from_text)

        if action_spec.output_type in CHOICE_ACTION_TYPES:

            def finish_choice(result: Any) -> ActionOutput:
                _idx, choice, raw = result
                return ActionOutput.choice_response(choice, raw=raw)

            return _ModelCallPlan(
                "sample_choice", (prompt, action_spec.options), model_kwargs, finish_choice
            )

        if action_spec.output_type == OutputType.FLOAT:
            if not callable(getattr(self.model, "sample_float", None)):
                raise NotImplementedError("Language model does not support sample_float().")

            def finish_float(value: Any) -> ActionOutput:
                try:
                    normalized = float(value)
                except ValueError as exc:
                    raise ValueError(f"Model did not return a valid float: {value!r}") from exc
                return ActionOutput.float_response(normalized, raw=value)

            return _ModelCallPlan("sample_float", (prompt,), model_kwargs, finish_float)

        if action_spec.output_type == OutputType.TOOL_CALLS:
            tools = action_spec.extra_args.get("tools")
            if not isinstance(tools, list) or not tools:
                raise ValueError("TOOL_CALLS action spec requires extra_args['tools'].")

            def finish_tool_calls(calls: Any) -> ActionOutput:
                if not calls:
                    raise ValueError("Model returned no tool calls for TOOL_CALLS action spec.")
                return ActionOutput.from_tool_calls(calls, raw=calls)

            return _ModelCallPlan(
                "sample_tool_calls",
                (prompt, tools),
                {"mode": str(action_spec.extra_args.get("tool_mode") or "single"), **model_kwargs},
                finish_tool_calls,
            )

        if action_spec.output_type == OutputType.STRUCTURED:
            schema = action_spec.extra_args.get("schema")
            if not isinstance(schema, dict) or not schema:
                raise ValueError("STRUCTURED action spec requires extra_args['schema'].")

            def finish_structured(payload: Any) -> ActionOutput:
                if not isinstance(payload, dict):
                    raise TypeError("sample_structured must return a dict.")
                return ActionOutput.structured_response(payload, raw=payload)

            return _ModelCallPlan(
                "sample_structured", (prompt, schema), model_kwargs, finish_structured
            )

        raise NotImplementedError(f"Unsupported output type: {action_spec.output_type}")

    def _call_model(self, context: str, action_spec: ActionSpec) -> ActionOutput:
        """Route one curated agent context and action spec to the bound model.

        Agent subclasses should build their local context inside ``act()`` and
        then call this helper to sample the correct model method for the
        requested output type.
        """
        plan = self._plan_model_call(context, action_spec)
        if not isinstance(plan, _ModelCallPlan):
            return plan
        return plan.finish(getattr(self.model, plan.method)(*plan.args, **plan.kwargs))

    async def _call_model_async(self, context: str, action_spec: ActionSpec) -> ActionOutput:
        """Async twin of :meth:`_call_model` for ``act_async`` implementations.

        Awaits the model's ``<method>_async`` twin when it provides one (native
        or the thread-wrapping default on :class:`LanguageModel`); a duck-typed
        model without async twins falls back to its sync method on a helper
        thread, so any model works here.
        """
        plan = self._plan_model_call(context, action_spec)
        if not isinstance(plan, _ModelCallPlan):
            return plan
        async_fn = getattr(self.model, f"{plan.method}_async", None)
        if callable(async_fn):
            return plan.finish(await async_fn(*plan.args, **plan.kwargs))
        return plan.finish(
            await asyncio.to_thread(getattr(self.model, plan.method), *plan.args, **plan.kwargs)
        )


def _join_context_and_prompt(context: str, prompt: str) -> str:
    """Join curated context and action prompt with stable spacing."""
    context_text = str(context or "").strip()
    prompt_text = str(prompt or "").strip()
    if context_text and prompt_text:
        return f"{context_text}\n\n{prompt_text}"
    return context_text or prompt_text
