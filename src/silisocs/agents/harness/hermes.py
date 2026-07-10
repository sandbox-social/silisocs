"""Hermes Agent (Nous Research) as a silisocs harness.

Hermes is a pure-Python assistant harness embeddable via ``from run_agent import
AIAgent``; one ``run_conversation()`` call is one full agentic turn (its inner tool loop
runs to completion, bounded by ``max_iterations``). This adapter maps that onto the
:class:`HarnessAdapter` contract:

- **Tools**: Hermes' tool registry is process-global, so tool schemas can't vary per
  turn. We register one generic ``silisocs_action(action_name, arguments)`` tool whose
  handler dispatches to the *current* :class:`ToolSurface` (held in a ContextVar set
  around each ``run_conversation``). The per-turn message lists the backend actions the
  agent may call, so the model knows the vocabulary. This is thread/async-task safe and
  sidesteps the global-registry-cross-talk risk with two concurrent agents.
- **Model plane**: the ``AIAgent`` is built against the run's Model Proxy
  (``base_url`` + the agent's routing token as ``api_key``, chat-completions mode), so
  all Hermes model calls flow through the unified telemetry plane. Real provider keys
  never enter Hermes.
- **State**: ``snapshot``/``restore`` round-trip the serialized ``conversation_history``
  (Hermes is snapshot-restored, never replayed).

The live wiring to the ``run_agent`` module is isolated behind the injectable
``aiagent_factory`` seam (the default imports Hermes lazily; tests inject a fake), so
this module imports and is contract-tested without the optional ``hermes`` extra. A real
``hermes-agent`` integration test is opt-in behind ``HERMES_LIVE=1``.

Dependency note: ``hermes-agent`` pins ``openai==2.24.0`` which conflicts with the
silisocs ``openai<2.0.0`` pin; install it in an isolated environment (or after the
openai 2.x migration). See ``docs/harness_agents.md``.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import Callable
from typing import Any

from silisocs.agents.harness.adapter import HarnessProbeRequest, HarnessTurnRequest
from silisocs.agents.harness.base import HarnessAgent, compose_persona
from silisocs.agents.harness.bridge import ToolSurface
from silisocs.agents.harness.types import HarnessTurnResult, extract_text, extract_usage
from silisocs.runtime.language_models import LanguageModel

# The ToolSurface for the turn currently running on this thread/task. Isolated per
# thread AND per asyncio task, so concurrent Hermes agents never cross tool state.
_CURRENT_SURFACE: contextvars.ContextVar[ToolSurface | None] = contextvars.ContextVar(
    "hermes_current_surface", default=None
)

AIAgentFactory = Callable[..., Any]

# Keys a Hermes ``run_conversation`` result may carry its closing message under.
_HERMES_TEXT_KEYS = ("output", "final", "content", "response")


def _hermes_final_text(result: Any) -> str:
    """Hermes' closing turn text: a direct key, else the last assistant message.

    ``run_conversation`` returns the whole conversation as ``{"messages": [...]}``, so the
    turn's text is the trailing assistant message. Some variants surface a direct key
    instead; fall back to the shared str/keyed extraction for anything else.
    """
    if isinstance(result, dict):
        for key in _HERMES_TEXT_KEYS:
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value
        messages = result.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if isinstance(message, dict) and str(message.get("role")) == "assistant":
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content
    return extract_text(result, _HERMES_TEXT_KEYS)


def _execute_current_surface(action_name: str, arguments: Any) -> str:
    """Handler for the generic ``silisocs_action`` tool: dispatch to the live surface."""
    surface = _CURRENT_SURFACE.get()
    if surface is None:
        return "No active tool surface; the harness may only act during its turn."
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return f"Could not parse arguments for '{action_name}': expected a JSON object."
    if not isinstance(arguments, dict):
        arguments = {}
    return surface.execute(str(action_name), arguments).result


def _default_aiagent_factory(**kwargs: Any) -> Any:
    """Build a real Hermes ``AIAgent`` and register the silisocs tool (lazy import).

    This is the single live-integration seam. It imports ``run_agent`` only when a real
    Hermes agent is actually constructed, and registers the generic ``silisocs_action``
    tool (routing to :func:`_execute_current_surface`) with Hermes' process-global tool
    registry. Kept isolated so the rest of the adapter is testable without the extra.
    """
    from run_agent import AIAgent  # type: ignore[import-not-found]

    try:  # register the tool once; ignore if the running Hermes already has it
        from tools import registry  # type: ignore[import-not-found]

        registry.register(
            name="silisocs_action",
            description=(
                "Perform one action in the silisocs social environment. "
                "Pass the action name and its arguments."
            ),
            handler=_execute_current_surface,
            toolset="silisocs",
            override=True,
        )
    except Exception:  # pragma: no cover - depends on the installed Hermes surface
        pass

    return AIAgent(
        model=kwargs["model_name"],
        base_url=kwargs["base_url"],
        api_key=kwargs["api_key"],
        api_mode=kwargs.get("api_mode", "chat_completions"),
        enabled_toolsets=["silisocs"],
        skip_memory=True,
        skip_context_files=True,
        quiet_mode=True,
        max_iterations=int(kwargs.get("max_iterations", 4)),
        session_id=kwargs.get("session_id", ""),
        ephemeral_system_prompt=kwargs.get("ephemeral_system_prompt", ""),
    )


class HermesAdapter:
    """Drive a Hermes ``AIAgent`` as a silisocs harness turn."""

    def __init__(
        self,
        *,
        name: str,
        persona: str = "",
        model_name: str = "gpt-4o-mini",
        max_iterations: int = 4,
        api_mode: str = "chat_completions",
        aiagent_factory: AIAgentFactory | None = None,
    ) -> None:
        self._name = str(name)
        self._persona = str(persona or "")
        self._model_name = str(model_name)
        self._max_iterations = int(max_iterations)
        self._api_mode = str(api_mode)
        self._factory = aiagent_factory or _default_aiagent_factory
        self._base_url = ""
        self._api_key = ""
        self._agent: Any = None
        self._history: list[Any] = []

    def bind_model_proxy(self, base_url: str, api_key: str) -> None:
        self._base_url = str(base_url)
        self._api_key = str(api_key)
        self._agent = None  # rebuild against the proxy on next turn

    def _ensure_agent(self) -> Any:
        if self._agent is None:
            self._agent = self._factory(
                model_name=self._model_name,
                base_url=self._base_url,
                api_key=self._api_key,
                api_mode=self._api_mode,
                ephemeral_system_prompt=self._persona,
                max_iterations=self._max_iterations,
                session_id=f"silisocs-{self._name}",
            )
        return self._agent

    @staticmethod
    def _compose_message(request: HarnessTurnRequest) -> str:
        parts: list[str] = []
        if request.observations.strip():
            parts.append(request.observations.strip())
        if request.prompt.strip():
            parts.append(request.prompt.strip())
        if request.surface is not None:
            names = [n for n in request.surface.tool_names() if n]
            if names:
                parts.append(
                    "Available actions (call the silisocs_action tool with one of these "
                    "names and its arguments): " + ", ".join(names)
                )
        return "\n\n".join(parts)

    def run_turn(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        agent = self._ensure_agent()
        message = self._compose_message(request)
        token = _CURRENT_SURFACE.set(request.surface)
        try:
            result = agent.run_conversation(message, conversation_history=self._history)
        finally:
            _CURRENT_SURFACE.reset(token)
        if isinstance(result, dict) and isinstance(result.get("messages"), list):
            self._history = list(result["messages"])
        return HarnessTurnResult(
            final_text=_hermes_final_text(result),
            usage=extract_usage(result),
            finished=True,
            raw=result,
        )

    def run_probe(self, request: HarnessProbeRequest) -> str:
        agent = self._ensure_agent()
        instruction = request.prompt
        if request.options:
            instruction += "\nAnswer with exactly one of: " + ", ".join(request.options)
        token = _CURRENT_SURFACE.set(None)  # no tools during a probe
        try:
            result = agent.run_conversation(instruction, conversation_history=[])
        finally:
            _CURRENT_SURFACE.reset(token)
        return _hermes_final_text(result)

    def snapshot(self) -> dict[str, Any]:
        return {"history": list(self._history)}

    def restore(self, state: dict[str, Any]) -> None:
        self._history = list(state.get("history", []) or [])


class HermesAgent(HarnessAgent):
    """A silisocs agent backed by a Hermes ``AIAgent``.

    Referenceable via ``persona_pipeline.classes.<class>.class_path``:
    ``silisocs.agents.harness.hermes.HermesAgent``. The persona-pipeline fields seed the
    Hermes ``ephemeral_system_prompt``; the model name comes from the per-class model
    config (routed through the Model Proxy).
    """

    def __init__(
        self,
        model: LanguageModel,
        *,
        name: str,
        persona: str = "",
        probe_mode: str = "model",
        hermes_model: str | None = None,
        max_iterations: int = 4,
        api_mode: str = "chat_completions",
        aiagent_factory: AIAgentFactory | None = None,
        **persona_fields: Any,
    ) -> None:
        model_name = str(hermes_model or getattr(model, "_model_name", "") or "gpt-4o-mini")
        composed_persona = compose_persona(persona, **persona_fields)
        adapter = HermesAdapter(
            name=name,
            persona=composed_persona,
            model_name=model_name,
            max_iterations=max_iterations,
            api_mode=api_mode,
            aiagent_factory=aiagent_factory,
        )
        super().__init__(
            model, name=name, adapter=adapter, persona=composed_persona, probe_mode=probe_mode
        )
