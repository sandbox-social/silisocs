"""Agent base classes and interfaces.

This module declares the abstract :class:`silisocs.agents.base_agent.Agent` interface used by the
simulation runtime as well as helper classes and lightweight concrete
implementations used by the project.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from silisocs.runtime.language_models import LanguageModel
    from silisocs.runtime.types import ActionOutput, ActionSpec


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

    def _call_model(self, context: str, action_spec: ActionSpec) -> ActionOutput:
        """Route one curated agent context and action spec to the bound model.

        Agent subclasses should build their local context inside ``act()`` and
        then call this helper to sample the correct model method for the
        requested output type.
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
            return ActionOutput.from_text(
                self.model.sample_text(
                    prompt,
                    **model_kwargs,
                )
            )

        if action_spec.output_type in CHOICE_ACTION_TYPES:
            idx, choice, raw = self.model.sample_choice(
                prompt,
                action_spec.options,
                **model_kwargs,
            )
            del idx
            return ActionOutput.choice_response(choice, raw=raw)

        if action_spec.output_type == OutputType.FLOAT:
            sample_float = getattr(self.model, "sample_float", None)
            if not callable(sample_float):
                raise NotImplementedError("Language model does not support sample_float().")
            value = sample_float(prompt, **model_kwargs)
            try:
                normalized = float(value)
            except ValueError as exc:
                raise ValueError(f"Model did not return a valid float: {value!r}") from exc
            return ActionOutput.float_response(normalized, raw=value)

        if action_spec.output_type == OutputType.TOOL_CALLS:
            tools = action_spec.extra_args.get("tools")
            if not isinstance(tools, list) or not tools:
                raise ValueError("TOOL_CALLS action spec requires extra_args['tools'].")
            calls = self.model.sample_tool_calls(
                prompt,
                tools,
                mode=str(action_spec.extra_args.get("tool_mode") or "single"),
                **model_kwargs,
            )
            if not calls:
                raise ValueError("Model returned no tool calls for TOOL_CALLS action spec.")
            return ActionOutput.from_tool_calls(calls, raw=calls)

        if action_spec.output_type == OutputType.STRUCTURED:
            schema = action_spec.extra_args.get("schema")
            if not isinstance(schema, dict) or not schema:
                raise ValueError("STRUCTURED action spec requires extra_args['schema'].")
            payload = self.model.sample_structured(prompt, schema, **model_kwargs)
            if not isinstance(payload, dict):
                raise TypeError("sample_structured must return a dict.")
            return ActionOutput.structured_response(payload, raw=payload)

        raise NotImplementedError(f"Unsupported output type: {action_spec.output_type}")


def _join_context_and_prompt(context: str, prompt: str) -> str:
    """Join curated context and action prompt with stable spacing."""
    context_text = str(context or "").strip()
    prompt_text = str(prompt or "").strip()
    if context_text and prompt_text:
        return f"{context_text}\n\n{prompt_text}"
    return context_text or prompt_text


# Type alias for flexibility
AgentLike = Agent
