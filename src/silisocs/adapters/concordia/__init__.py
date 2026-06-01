"""Explicit Concordia compatibility boundary for legacy extensions."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.runtime.language_models import LanguageModel
from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType, ToolCall

_INSTALL_HINT = "Install Concordia compatibility with `pip install 'silisocs[concordia]'`."

try:
    from concordia.agents import entity_agent_with_logging
    from concordia.associative_memory import basic_associative_memory
    from concordia.components.agent import (
        action_spec_ignored,
        concat_act_component,
        constant,
        instructions,
        observation,
        question_of_recent_memories,
        scripted_act,
    )
    from concordia.components.agent import memory as memory_component
    from concordia.components.game_master import make_observation as make_observation_component
    from concordia.document import interactive_document
    from concordia.typing import entity as concordia_entity
    from concordia.typing import entity_component
    from concordia.typing import prefab as concordia_prefab
except ImportError as exc:  # pragma: no cover - depends on optional extra
    raise ImportError(_INSTALL_HINT) from exc

from silisocs.adapters.concordia.action_spec_parser import action_spec_parser
from silisocs.adapters.concordia.memory import (
    ListMemoryBank,
    create_memory_bank,
    deterministic_sentence_embedder,
)
from silisocs.runtime import language_models as language_model


def make_concordia_memory_bank(memory_backend: str = "list") -> Any:
    """Return a Concordia-compatible memory bank for compat prefabs."""
    return create_memory_bank(memory_backend)


def _native_to_concordia_spec(spec: ActionSpec) -> Any:
    output_map = {
        OutputType.TEXT: concordia_entity.OutputType.FREE,
        OutputType.CHOICE: concordia_entity.OutputType.CHOICE,
        OutputType.FLOAT: concordia_entity.OutputType.FLOAT,
        OutputType.SKIP: concordia_entity.OutputType.FREE,
        OutputType.TOOL_CALLS: concordia_entity.OutputType.FREE,
        OutputType.STRUCTURED: concordia_entity.OutputType.FREE,
    }
    return concordia_entity.ActionSpec(
        call_to_action=spec.prompt,
        output_type=output_map[spec.output_type],
        options=spec.options,
        tag=spec.tag,
    )


def _normalize_legacy_action(result: Any, spec: ActionSpec | None = None) -> ActionOutput:
    if isinstance(result, ActionOutput):
        return result
    text = str(result or "")
    if spec is not None and spec.output_type == OutputType.CHOICE:
        return ActionOutput.choice_response(text, raw=result)
    if spec is not None and spec.output_type == OutputType.FLOAT:
        try:
            return ActionOutput.float_response(float(text.strip()), raw=result)
        except ValueError:
            return ActionOutput.from_text(text, raw=result)
    return ActionOutput.from_text(text, raw=result)


def extract_structured_response(text: str) -> dict[str, Any] | None:
    """Extract a structured-response payload from legacy Concordia text."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    nested = parsed.get("structured_response")
    return nested if isinstance(nested, dict) else parsed


class ConcordiaAgentAdapter(Agent):
    """Wrap a Concordia-style agent for native engine use."""

    def __init__(self, target: Any, model: LanguageModel) -> None:
        super().__init__(model)
        self._target = target

    @property
    def name(self) -> str:
        return str(getattr(self._target, "name", getattr(self._target, "_agent_name", "agent")))

    def observe(self, observation: str) -> None:
        observer = getattr(self._target, "observe", None)
        if callable(observer):
            observer(str(observation))

    def initialize(self, context: Any | None = None) -> None:
        initializer = getattr(self._target, "initialize", None)
        if callable(initializer):
            initializer(context)

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        act = getattr(self._target, "act", None)
        if not callable(act):
            raise TypeError("ConcordiaAgentAdapter target must expose act(action_spec).")
        return _normalize_legacy_action(act(_native_to_concordia_spec(action_spec)), action_spec)

    def get_last_log(self) -> Mapping[str, Any]:
        getter = getattr(self._target, "get_last_log", None)
        return dict(getter()) if callable(getter) else {}

    def get_state(self) -> Mapping[str, Any]:
        getter = getattr(self._target, "get_state", None)
        return dict(getter()) if callable(getter) else {}

    def set_state(self, state: Mapping[str, Any]) -> None:
        setter = getattr(self._target, "set_state", None)
        if callable(setter):
            setter(state)


class ConcordiaGameMasterAdapter:
    """Adapt a Concordia-shaped GM to the native GM protocol."""

    def __init__(self, target: Any) -> None:
        self._target = target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    @property
    def name(self) -> str:
        return str(getattr(self._target, "name", "game_master"))

    @staticmethod
    def _names_from_result(result: Any) -> list[str]:
        if isinstance(result, str):
            return [name.strip() for name in result.split(",") if name.strip()]
        if isinstance(result, Sequence):
            return [
                str(getattr(item, "name", item)).strip() for item in result if str(item).strip()
            ]
        return []

    def acting_agents(self, candidate_agents: Sequence[Any]) -> list[str]:
        direct = getattr(self._target, "acting_agents", None)
        if callable(direct):
            result = direct(candidate_agents)
        else:
            next_acting = getattr(self._target, "next_acting", None)
            if not callable(next_acting):
                raise TypeError("Adapted Concordia GM must expose acting_agents/next_acting.")
            result = next_acting(candidate_agents)
        candidates = {agent.name for agent in candidate_agents}
        return [name for name in self._names_from_result(result) if name in candidates]

    def initialize(self, *, agents: Sequence[Any], context: Any) -> None:
        direct = getattr(self._target, "initialize", None)
        if callable(direct):
            direct(agents=agents, context=context)

    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        direct = getattr(self._target, "update", None)
        if callable(direct):
            direct(step=step, agents=agents, context=context)

    def action_prompt(self, agent_name: str) -> ActionSpec:
        prompt = getattr(self._target, "action_prompt", None)
        if callable(prompt):
            result = prompt(agent_name)
            if isinstance(result, ActionSpec):
                return result
            parsed = action_spec_parser(result)
            return ActionSpec(
                prompt=parsed.prompt,
                output_type=parsed.output_type,
                options=parsed.options,
                tag=parsed.tag,
                extra_args=parsed.extra_args,
            )
        raise TypeError("Adapted Concordia GM must expose action_prompt(agent_name).")

    def make_observation(self, agent_name: str) -> str:
        direct = getattr(self._target, "make_observation", None)
        if callable(direct):
            return str(direct(agent_name))
        raise TypeError("Adapted Concordia GM must expose make_observation(agent_name).")

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        direct = getattr(self._target, "resolve_action", None)
        if callable(direct):
            return str(direct(agent_name, action))
        raise TypeError("Adapted Concordia GM must expose resolve_action(agent_name, action).")


class SocialConcatActComponent(concat_act_component.ConcatActComponent):
    """Concordia act component extended to emit tool/structured native payloads."""

    @staticmethod
    def _format_prompt(prompt: str, name: str) -> str:
        escaped = str(prompt or "").replace("{", "{{").replace("}", "}}")
        escaped = escaped.replace("{{name}}", "{name}")
        try:
            return escaped.format(name=name)
        except (KeyError, ValueError):
            return escaped.replace("{{", "{").replace("}}", "}")

    def get_action_attempt(self, contexts: Mapping[str, str], action_spec: Any) -> Any:
        native_output_type = getattr(action_spec, "output_type", None)
        native_extra_args = dict(getattr(action_spec, "extra_args", {}) or {})
        if native_output_type == OutputType.TOOL_CALLS:
            tools = list(native_extra_args.get("tools") or [])
            if not tools:
                raise ValueError("TOOL_CALLS action spec requires tools.")
            prompt = self._context_for_action(contexts)
            prompt = (
                f"{prompt}\n\n{self._format_prompt(action_spec.prompt, self.get_entity().name)}"
            )
            calls = self._model.sample_tool_calls(
                prompt,
                tools,
                mode=str(native_extra_args.get("tool_mode") or "single"),
            )
            normalized = [ToolCall(call.name, dict(call.arguments)) for call in calls]
            return ActionOutput.from_tool_calls(normalized, raw=calls)
        if native_output_type == OutputType.STRUCTURED:
            schema = dict(native_extra_args.get("schema") or {})
            if not schema:
                raise ValueError("STRUCTURED action spec requires schema.")
            prompt = self._context_for_action(contexts)
            prompt = (
                f"{prompt}\n\n{self._format_prompt(action_spec.prompt, self.get_entity().name)}"
            )
            payload = self._model.sample_structured(prompt, schema)
            return ActionOutput.structured_response(payload, raw=payload)
        result = super().get_action_attempt(contexts, action_spec)
        return result


entity_lib = SimpleNamespace(
    ActionSpec=concordia_entity.ActionSpec,
    OutputType=concordia_entity.OutputType,
    FREE_ACTION_TYPES=concordia_entity.FREE_ACTION_TYPES,
    CHOICE_ACTION_TYPES=concordia_entity.CHOICE_ACTION_TYPES,
)
ComponentContextMapping = dict[str, entity_component.ContextComponent]
ComponentEntity = entity_agent_with_logging.EntityAgentWithLogging
ScriptedActComponent = scripted_act.ScriptedActComponent
ActionSpecIgnored = action_spec_ignored.ActionSpecIgnored
entity_component = entity_component
prefab_lib = concordia_prefab
memory_comp = memory_component
EntityAgentWithLogging = entity_agent_with_logging.EntityAgentWithLogging
entity_agent_with_logging = entity_agent_with_logging
interactive_document = interactive_document
constant = constant
instructions = instructions
observation = observation
question_of_recent_memories = question_of_recent_memories
make_observation_component = make_observation_component
concordia_location = "gdm-concordia"

__all__ = [
    "ActionSpecIgnored",
    "ComponentContextMapping",
    "ComponentEntity",
    "ConcordiaAgentAdapter",
    "ConcordiaGameMasterAdapter",
    "EntityAgentWithLogging",
    "ListMemoryBank",
    "ScriptedActComponent",
    "SocialConcatActComponent",
    "action_spec_ignored",
    "basic_associative_memory",
    "concat_act_component",
    "concordia_location",
    "constant",
    "create_memory_bank",
    "deterministic_sentence_embedder",
    "entity_agent_with_logging",
    "entity_component",
    "entity_lib",
    "extract_structured_response",
    "interactive_document",
    "language_model",
    "make_concordia_memory_bank",
    "make_observation_component",
    "memory_comp",
    "observation",
    "prefab_lib",
    "question_of_recent_memories",
    "scripted_act",
]
