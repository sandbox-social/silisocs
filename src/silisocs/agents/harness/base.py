"""``HarnessAgent`` — a silisocs ``Agent`` backed by a real agent harness.

A harness agent runs its *own* agentic loop inside one :meth:`act` call: the harness
picks a tool, the Tool Bridge executes it against the backend and returns the real
result, the harness continues, and eventually finishes — many model/tool calls, one
``ActionOutput``. That inverts where the loop lives (the harness owns it, not our turn
policy), so for a harness class the effective turn policy is ``single_action`` around
one complete harness run.

This class is backend/GM/engine-agnostic: it reads the per-turn :class:`ToolSurface`
from the action spec (bound by the ``harness`` action-prompt component), buffers
observations, dispatches probes, snapshots adapter state for checkpoints, and packages
the turn into an ``ActionOutput`` whose ``structured['harness_turn']`` payload the
``harness`` resolve component records. Everything harness-specific lives behind the
:class:`HarnessAdapter`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.agents.harness.adapter import HarnessProbeRequest, HarnessTurnRequest
from silisocs.agents.harness.bridge import ToolSurface
from silisocs.agents.harness.types import ExecutedToolCall, HarnessTurnResult
from silisocs.runtime.language_models import LanguageModel
from silisocs.runtime.types import HARNESS_TURN_KEY, ActionOutput, ActionSpec, OutputType

# Output types answered via the probe path rather than a harness turn.
_PROBE_OUTPUT_TYPES = frozenset({OutputType.CHOICE, OutputType.STRUCTURED, OutputType.FLOAT})


def summarize_executed(executed: tuple[ExecutedToolCall, ...]) -> str:
    """A short human-readable summary of a turn's tool calls (fallback action text)."""
    if not executed:
        return "Harness turn produced no actions."
    parts = [call.name + ("" if call.ok else " (failed)") for call in executed]
    return "Executed: " + ", ".join(parts)


# Persona-pipeline fields a concrete harness agent folds into its persona/SOUL text, in
# render order. Concrete agents (FakeHarnessAgent, HermesAgent, ...) accept the full
# persona-pipeline param surface via ``**persona_fields`` and call this so the harness's
# identity is seeded from the same declarative persona data native agents use.
_PERSONA_FIELDS = ("context", "persona_context", "bio", "goal", "style", "instructions")


def compose_persona(explicit: str = "", **persona_fields: Any) -> str:
    """Build a persona/SOUL string from declarative persona-pipeline fields."""
    parts: list[str] = []
    if str(explicit or "").strip():
        parts.append(str(explicit).strip())
    for key in _PERSONA_FIELDS:
        value = str(persona_fields.get(key) or "").strip()
        if value:
            parts.append(value)
    memories = persona_fields.get("shared_memories") or []
    specific = persona_fields.get("specific_memories") or []
    for line in list(memories) + list(specific):
        text = str(line or "").strip()
        if text:
            parts.append(text)
    # Deduplicate while preserving order (personas often repeat world context).
    return "\n\n".join(dict.fromkeys(parts))


class HarnessAgent(Agent):
    """An ``Agent`` whose turns are run by an embedded/out-of-process harness."""

    # Duck-typed marker read by the default action-prompt component: agents that want a
    # per-turn Tool Bridge get one bound into ``ActionSpec.extra_args['tool_surface']``.
    # Native agents lack this attribute, so nothing changes for non-harness runs.
    wants_tool_surface: bool = True

    def __init__(
        self,
        model: LanguageModel,
        *,
        name: str,
        adapter: Any,
        persona: str = "",
        probe_mode: str = "model",
    ) -> None:
        super().__init__(model)
        self._name = str(name)
        self._adapter = adapter
        self._persona = str(persona or "")
        self._probe_mode = str(probe_mode or "model").strip().lower()
        if self._probe_mode not in {"model", "harness"}:
            raise ValueError("probe_mode must be 'model' or 'harness'.")
        self._pending_observations: list[str] = []
        self._last_turn_summary: str = ""

    @property
    def name(self) -> str:
        return self._name

    @property
    def adapter(self) -> Any:
        return self._adapter

    def bind_model_proxy(self, base_url: str, api_key: str) -> None:
        """Point this agent's harness at the run's Model Proxy (base_url + routing token).

        Called once at runtime assembly after the proxy is up (the port is allocated
        then). Forwarded to the adapter when it exposes ``bind_model_proxy`` — real
        adapters (Hermes/OpenClaw) build their model client against the proxy so all
        harness model calls flow through the unified telemetry plane; the fake adapter
        makes no model calls and ignores it.
        """
        binder = getattr(self._adapter, "bind_model_proxy", None)
        if callable(binder):
            binder(base_url, api_key)

    # ------------------------------------------------------------------ #
    # Observation buffering
    # ------------------------------------------------------------------ #

    def observe(self, observation: str) -> None:
        text = str(observation or "").strip()
        if text:
            self._pending_observations.append(text)

    def _drain_observations(self) -> str:
        text = "\n".join(self._pending_observations)
        self._pending_observations = []
        return text

    # ------------------------------------------------------------------ #
    # Acting
    # ------------------------------------------------------------------ #

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        if action_spec.output_type in _PROBE_OUTPUT_TYPES:
            return self._act_probe(action_spec)
        request = self._build_turn_request(action_spec)
        result = self._coerce_turn_result(self._adapter.run_turn(request))
        return self._turn_to_output(result, request.surface)

    async def act_async(self, action_spec: ActionSpec) -> ActionOutput:
        if action_spec.output_type in _PROBE_OUTPUT_TYPES:
            return await asyncio.to_thread(self._act_probe, action_spec)
        request = self._build_turn_request(action_spec)
        run_turn_async = getattr(self._adapter, "run_turn_async", None)
        if callable(run_turn_async):
            result = self._coerce_turn_result(await run_turn_async(request))
            return self._turn_to_output(result, request.surface)
        result = self._coerce_turn_result(await asyncio.to_thread(self._adapter.run_turn, request))
        return self._turn_to_output(result, request.surface)

    def _build_turn_request(self, action_spec: ActionSpec) -> HarnessTurnRequest:
        surface = action_spec.extra_args.get("tool_surface")
        if surface is not None and not isinstance(surface, ToolSurface):
            surface = None
        return HarnessTurnRequest(
            agent_name=self._name,
            prompt=action_spec.prompt,
            observations=self._drain_observations(),
            surface=surface,
        )

    @staticmethod
    def _coerce_turn_result(result: Any) -> HarnessTurnResult:
        if isinstance(result, HarnessTurnResult):
            return result
        # An adapter that returns a bare string is treated as a tool-free text turn.
        return HarnessTurnResult(final_text=str(result or ""))

    def _turn_to_output(
        self, result: HarnessTurnResult, surface: ToolSurface | None
    ) -> ActionOutput:
        # The Tool Bridge is the authoritative record of what executed (every call
        # routes through it); fall back to the adapter's own list only when no surface
        # was bound (tool-free turns).
        executed = tuple(surface.executed) if surface is not None else tuple(result.executed)
        text = result.final_text.strip() or summarize_executed(executed)
        self._last_turn_summary = text
        payload = {
            HARNESS_TURN_KEY: {
                "final_text": result.final_text,
                "executed": [call.to_dict() for call in executed],
                "usage": dict(result.usage),
                "finished": bool(result.finished),
                "tool_calls": len(executed),
                "failures": sum(1 for call in executed if not call.ok),
            }
        }
        return ActionOutput(output_type=OutputType.TEXT, text=text, structured=payload)

    # ------------------------------------------------------------------ #
    # Probes
    # ------------------------------------------------------------------ #

    def _act_probe(self, action_spec: ActionSpec) -> ActionOutput:
        run_probe = getattr(self._adapter, "run_probe", None)
        if self._probe_mode == "harness" and callable(run_probe):
            request = HarnessProbeRequest(
                agent_name=self._name,
                prompt=action_spec.prompt,
                options=tuple(action_spec.options),
            )
            answer = str(run_probe(request) or "")
            return self._probe_answer_to_output(action_spec, answer)
        # Default 'model' mode: answer through our own LanguageModel with a summary of
        # the agent's state as context, so probe usage is fully telemetered.
        return self._call_model(self._probe_context(), action_spec)

    def _probe_answer_to_output(self, action_spec: ActionSpec, answer: str) -> ActionOutput:
        output_type = action_spec.output_type
        if output_type == OutputType.CHOICE:
            options = list(action_spec.options)
            lowered = answer.strip().lower()
            for option in options:
                if str(option).strip().lower() in lowered:
                    return ActionOutput.choice_response(str(option), raw=answer)
            return ActionOutput.choice_response(str(options[0]) if options else "", raw=answer)
        if output_type == OutputType.FLOAT:
            try:
                return ActionOutput.float_response(float(answer.strip()), raw=answer)
            except ValueError:
                return ActionOutput.float_response(0.0, raw=answer)
        if output_type == OutputType.STRUCTURED:
            import json

            try:
                parsed = json.loads(answer)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                return ActionOutput.structured_response(parsed, raw=answer)
            return ActionOutput.structured_response({"answer": answer}, raw=answer)
        return ActionOutput.from_text(answer, raw=answer)

    def _probe_context(self) -> str:
        parts: list[str] = []
        if self._persona:
            parts.append(self._persona)
        if self._last_turn_summary:
            parts.append(f"Your most recent activity: {self._last_turn_summary}")
        if self._pending_observations:
            parts.append("Recent observations:\n" + "\n".join(self._pending_observations[-5:]))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ #
    # Checkpoint state (snapshot only — harnesses are never replay-restored)
    # ------------------------------------------------------------------ #

    def get_state(self) -> dict[str, Any]:
        snapshot = getattr(self._adapter, "snapshot", None)
        adapter_state = dict(snapshot()) if callable(snapshot) else {}
        return {
            "observations": list(self._pending_observations),
            "last_turn_summary": self._last_turn_summary,
            "adapter": adapter_state,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        if not state:
            return
        self._pending_observations = [str(item) for item in state.get("observations", [])]
        self._last_turn_summary = str(state.get("last_turn_summary", "") or "")
        restore = getattr(self._adapter, "restore", None)
        adapter_state = state.get("adapter")
        if callable(restore) and isinstance(adapter_state, dict):
            restore(adapter_state)
