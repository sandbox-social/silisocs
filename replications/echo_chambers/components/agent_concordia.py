"""Concordia-component EchoChamberSim agent prototype.

This module is intentionally not the default replication agent.  It provides a
more modular Concordia entity that preserves the current custom agent's prompt
and update sequence while making memory processors swappable in a later pass.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import re
import time
from collections.abc import Mapping
from typing import Any

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import agent as agent_components
from concordia.document import interactive_document
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from concordia.typing import prefab as prefab_lib
from pydantic import BaseModel

from replications.echo_chambers.components.app import extract_observation
from silisocs.agents.components.concat_act import (
    SocialConcatActComponent,
    extract_structured_response,
)
from silisocs.agents.entity import (
    DEFAULT_GOAL_COMPONENT_KEY,
    INSTRUCTIONS_COMPONENT_KEY,
    PERSONA_INFORMATION_KEY,
    SCENARIO_CONTEXT_KEY,
)
from silisocs.runtime.config import ConfigStore

MEMORY_COMPONENT_KEY = agent_components.memory.DEFAULT_MEMORY_COMPONENT_KEY
OBSERVATION_COMPONENT_KEY = "__Echo Observation Payload__"
SHORT_MEMORY_PROCESSOR_KEY = "__Echo Short Memory Processor__"
LONG_MEMORY_PROCESSOR_KEY = "__Echo Long Memory Processor__"
BELIEF_UPDATE_PROCESSOR_KEY = "__Echo Belief Update Processor__"
SOCIAL_MEMORY_STORE_KEY = "__Echo Social Short/Long Memory__"
ECHO_OBSERVATION_TAG = "ECHO_OBSERVATION"
ECHO_SHORT_MEMORY_TAG = "ECHO_SHORT_MEMORY"
ECHO_LONG_MEMORY_TAG = "ECHO_LONG_MEMORY"
ECHO_BELIEF_UPDATE_TAG = "ECHO_BELIEF_UPDATE"


class UpdateOpinionResponse(BaseModel):
    opinion: str
    belief: int
    reasoning: str


class ReflectingResponse(BaseModel):
    short_term_memory: str


class LongMemoryResponse(BaseModel):
    long_term_memory: str


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _latest_tagged(memory: agent_components.memory.Memory, tag: str) -> str:
    prefix = f"[{tag}] "
    matches = memory.scan(lambda text: str(text).startswith(prefix))
    if not matches:
        return ""
    return str(matches[-1]).removeprefix(prefix)


def _add_tagged(memory: agent_components.memory.Memory, tag: str, text: str) -> None:
    memory.add(f"[{tag}] {text}")


def _memories_since_last_long_memory(
    memory: agent_components.memory.Memory,
    *,
    observation_tag: str = ECHO_OBSERVATION_TAG,
    long_tag: str = ECHO_LONG_MEMORY_TAG,
) -> list[str]:
    all_memories = [str(item) for item in memory.get_all_memories_as_text()]
    long_prefix = f"[{long_tag}] "
    observation_prefix = f"[{observation_tag}] "
    last_long_idx = -1
    for idx, item in enumerate(all_memories):
        if item.startswith(long_prefix):
            last_long_idx = idx
    return [
        item.removeprefix(observation_prefix)
        for item in all_memories[last_long_idx + 1 :]
        if item.startswith(observation_prefix)
    ]


def _load_class(class_path: str, expected_base: type[Any]) -> type[Any]:
    module_name, _, class_name = class_path.rpartition(".")
    if not module_name or not class_name:
        raise ValueError(f"Invalid class path: {class_path}")
    cls = getattr(importlib.import_module(module_name), class_name)
    if not isinstance(cls, type) or not issubclass(cls, expected_base):
        raise TypeError(f"{class_path} must inherit from {expected_base.__name__}")
    return cls


def _sample_json(
    *,
    model: language_model.LanguageModel,
    system_prompt: str,
    task: str,
    user_prompt: str,
    schema_hint: str,
    response_type: type[BaseModel] | None,
    fallback: dict[str, Any],
    llm_response_mode: str,
    temperature: float | None,
) -> dict[str, Any]:
    json_instruction = (
        f"{task.strip()}\n\n"
        f"{user_prompt.strip()}\n\n"
        "Return only a valid JSON object. Do not wrap it in Markdown.\n"
        f"Required JSON schema: {schema_hint}"
    )
    client = getattr(model, "_client", None)
    model_name = getattr(model, "_model_name", None)
    if client is not None and model_name:
        max_retries = int(getattr(model, "_max_retries", 2))
        for attempt in range(max_retries + 1):
            try:
                use_structured_parse = response_type is not None and llm_response_mode in {
                    "structured_parse",
                    "parse",
                }
                messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": user_prompt if use_structured_parse else json_instruction,
                    },
                ]
                call_temperature = (
                    temperature if temperature is not None else getattr(model, "_temperature", 1.0)
                )
                if use_structured_parse:
                    response = client.beta.chat.completions.parse(
                        model=model_name,
                        messages=messages,
                        temperature=call_temperature,
                        response_format=response_type,
                        timeout=120,
                    )
                    parsed_object = response.choices[0].message.parsed
                    parsed = parsed_object.model_dump() if parsed_object else None
                    raw = json.dumps(parsed, ensure_ascii=True) if parsed else ""
                else:
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        temperature=call_temperature,
                        max_tokens=900,
                        timeout=120,
                        response_format={"type": "json_object"},
                    )
                    raw = response.choices[0].message.content or ""
                    parsed = _extract_json_object(raw)
                recorder = getattr(model, "_record_retry_outcome", None)
                if callable(recorder):
                    recorder(attempt, True)
                logger = getattr(model, "_log", None)
                if callable(logger):
                    logger(
                        f"SYSTEM:\n{system_prompt}\n\nUSER:\n{messages[1]['content']}",
                        raw,
                    )
                return parsed if parsed is not None else dict(fallback)
            except Exception:
                if attempt >= max_retries:
                    recorder = getattr(model, "_record_retry_outcome", None)
                    if callable(recorder):
                        recorder(attempt, False)
                    return dict(fallback)
                time.sleep(min(4.0, 1.0 + attempt))

    prompt = (
        f"{system_prompt.strip()}\n\n"
        f"{task.strip()}\n\n"
        f"{user_prompt.strip()}\n\n"
        "Return only a valid JSON object. Do not wrap it in Markdown.\n"
        f"Required JSON schema: {schema_hint}"
    )
    for _ in range(3):
        try:
            raw = model.sample_text(
                prompt,
                max_tokens=900,
                temperature=temperature,
                terminators=None,
            )
        except Exception:
            return dict(fallback)
        parsed = _extract_json_object(raw)
        if parsed is not None:
            return parsed
    return dict(fallback)


@dataclasses.dataclass(eq=False)
class EchoObservationPayload(entity_component.ContextComponent):
    """Stores the latest app observation payload for processor components."""

    payload: dict[str, Any] | None = None

    def pre_observe(self, observation: str) -> str:
        self.payload = extract_observation(observation)
        return ""

    def get_state(self) -> entity_component.ComponentState:
        return {"payload": json.dumps(self.payload or {}, ensure_ascii=True)}

    def set_state(self, state: entity_component.ComponentState) -> None:
        raw = str(state.get("payload", "{}") or "{}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        self.payload = parsed if isinstance(parsed, dict) else {}


@dataclasses.dataclass(eq=False)
class EchoProcessor(entity_component.ContextComponent):
    """Base processor carrying shared prompt and model settings."""

    model: language_model.LanguageModel
    system_prompt: str
    temperature: float | None = None
    llm_response_mode: str = "json_object"
    prompt_variant: str = "compat"

    def _sample(
        self,
        *,
        task: str,
        user_prompt: str,
        schema_hint: str,
        response_type: type[BaseModel] | None,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        return _sample_json(
            model=self.model,
            system_prompt=self.system_prompt,
            task=task,
            user_prompt=user_prompt,
            schema_hint=schema_hint,
            response_type=response_type,
            fallback=fallback,
            llm_response_mode=self.llm_response_mode,
            temperature=self.temperature,
        )

    def get_state(self) -> entity_component.ComponentState:
        return {}

    def set_state(self, state: entity_component.ComponentState) -> None:
        del state


class ShortMemoryProcessor(EchoProcessor):
    """Builds the EchoChamberSim short-term memory prompt."""

    def process(self, payload: dict[str, Any]) -> str:
        opinions = [
            str(item.get("opinion", ""))
            for item in payload.get("opinions_heard", [])
            if str(item.get("opinion", "")).strip()
        ]
        lines = [f"One of your close contacts believes: {opinion}" for opinion in opinions]
        for perspective in payload.get("passive_perspectives", []) or []:
            if str(perspective).strip():
                lines.append(f"You heard that: {perspective}")
        opinions_text = "\n".join(lines)
        if not opinions_text and self.prompt_variant == "compat":
            opinions_text = "No close contacts shared opinions today."
        user_prompt = (
            f"The opinions you have heard so far: {opinions_text}\n\n"
            "Task:\n"
            "Summarize the opinions provided to form your short-term memory.\n\n"
            "Instructions:\n"
            "- Do not add or create information that is not present in the provided opinions.\n"
            '- Start the summary with: "In my short-term memory, ..."\n'
            "- Provide a brief and accurate summary of the opinions shared with you."
        )
        if self.prompt_variant == "exact":
            user_prompt += (
                "\n\nOutput:\n- short_term_memory: Your summarized short-term memory statement."
            )
        parsed = self._sample(
            task="Short-term memory reflection",
            user_prompt=user_prompt,
            schema_hint='{"short_term_memory": "string"}',
            response_type=ReflectingResponse,
            fallback={"short_term_memory": f"In my short-term memory, {opinions_text}"},
        )
        return str(parsed.get("short_term_memory", "") or "")


class LongMemoryProcessor(EchoProcessor):
    """Builds the EchoChamberSim long-term memory prompt."""

    def process(self, payload: dict[str, Any], short_memory: str, previous_long: str) -> str:
        if not bool(payload.get("with_long_memory", True)):
            return previous_long
        user_prompt = (
            f"Recap of Previous Long-Term Memory: {previous_long}\n"
            f"Today's Short-Term Memory: {short_memory}\n\n"
            "Task:\n"
            "Using only the information in the previous long-term memory and today's short-term memory, "
            "create an updated long-term memory.\n\n"
            "Instructions:\n"
            "- Do not introduce any new information that is not present in the provided memories.\n"
            '- Start the updated memory with: "In my long-term memory, ..."\n'
            "- Accurately combine key details from both the long-term and short-term memories into a clear summary."
        )
        if self.prompt_variant == "exact":
            user_prompt += (
                "\n\nOutput:\n"
                "- long_term_memory: Your new, consolidated long-term memory statement."
            )
        parsed = self._sample(
            task="Long-term memory consolidation",
            user_prompt=user_prompt,
            schema_hint='{"long_term_memory": "string"}',
            response_type=LongMemoryResponse,
            fallback={
                "long_term_memory": f"In my long-term memory, {previous_long} {short_memory}".strip()
            },
        )
        return str(parsed.get("long_term_memory", "") or "")


class BeliefUpdateProcessor(EchoProcessor):
    """Builds the EchoChamberSim opinion/belief update prompt."""

    def process(
        self, payload: dict[str, Any], short_memory: str, long_memory: str
    ) -> dict[str, Any]:
        belief = int(payload.get("belief", 0) or 0)
        user_prompt = (
            f"Your previous opinion: {payload.get('opinion', '')}\n"
            f"Your previous belief value: {belief}\n"
            f"Your long-term memory: {long_memory}\n"
            "Belief values: '-2' for strongly oppose, '-1' for somewhat oppose, "
            "'0' for neutral, '1' for somewhat support, '2' for strongly support.\n\n"
            "Task:\n"
            "Reflect on your opinion and belief, considering whether to maintain your stance "
            "or adjust it based on your long-term memory.\n\n"
            "Instructions:\n"
            "- Think like a human: Decide whether to hold firm in your own opinion or adapt "
            "based on the influence of the opinions you have heard.\n\n"
            f"{'Output structure (in code format):' if self.prompt_variant == 'exact' else ''}\n\n"
            f"opinion: Provide your current opinion on the topic '{payload.get('topic', '')}' "
            f"in several sentences. Your opinion must contain one keyword from "
            f"{payload.get('belief_keywords', {})} that reflects your stance. It should begin with: "
            '"I {the selected keyword}"\n\n'
            "belief: Indicate your current belief value regarding the topic.\n\n"
            "reasoning: Explain the reasoning behind your opinion and belief, elaborating on "
            "whether you upheld your original stance or were influenced by the opinions in your long-term memory."
        )
        parsed = self._sample(
            task="Opinion and belief update",
            user_prompt=user_prompt,
            schema_hint='{"opinion": "string", "belief": "integer from -2 to 2", "reasoning": "string"}',
            response_type=UpdateOpinionResponse,
            fallback={
                "opinion": str(payload.get("opinion", "") or ""),
                "belief": belief,
                "reasoning": "No valid model update was produced.",
            },
        )
        try:
            new_belief = int(parsed.get("belief", belief))
        except (TypeError, ValueError):
            new_belief = belief
        new_belief = max(-2, min(2, new_belief))
        return {
            "episode": int(payload.get("episode", 0) or 0),
            "agent_name": str(payload.get("agent_name", "")),
            "agent_id": int(payload.get("agent_id", -1) or -1),
            "belief": new_belief,
            "opinion": str(parsed.get("opinion", payload.get("opinion", "")) or ""),
            "reasoning": str(parsed.get("reasoning", "") or ""),
            "short_term_memory": short_memory,
            "long_term_memory": long_memory,
            "contact_ids": list(payload.get("contact_ids", []) or []),
        }


class EchoChamberActComponent(entity_component.ActingComponent):
    """Sequentially orchestrates EchoChamberSim's three LLM procedure steps."""

    def get_action_attempt(
        self,
        context: entity_component.ComponentContextMapping,
        action_spec: entity_lib.ActionSpec,
    ) -> str:
        del context, action_spec
        entity = self.get_entity()
        observation = entity.get_component(OBSERVATION_COMPONENT_KEY, type_=EchoObservationPayload)
        memory = entity.get_component(MEMORY_COMPONENT_KEY, type_=agent_components.memory.Memory)
        short_processor = entity.get_component(
            SHORT_MEMORY_PROCESSOR_KEY, type_=ShortMemoryProcessor
        )
        long_processor = entity.get_component(LONG_MEMORY_PROCESSOR_KEY, type_=LongMemoryProcessor)
        belief_processor = entity.get_component(
            BELIEF_UPDATE_PROCESSOR_KEY, type_=BeliefUpdateProcessor
        )

        payload = observation.payload or {
            "episode": 0,
            "agent_name": entity.name,
            "agent_id": -1,
            "belief": 0,
            "opinion": "",
            "long_memory": "",
            "belief_keywords": {},
            "topic": "Should we use euthanasia?",
            "opinions_heard": [],
            "passive_perspectives": [],
            "contact_ids": [],
            "with_long_memory": True,
        }
        previous_long = _latest_tagged(memory, "ECHO_LONG_MEMORY")
        if not previous_long:
            previous_long = str(payload.get("long_memory", "") or "")

        short_memory = short_processor.process(payload)
        memory.add(f"[ECHO_SHORT_MEMORY] {short_memory}")
        long_memory = long_processor.process(payload, short_memory, previous_long)
        memory.add(f"[ECHO_LONG_MEMORY] {long_memory}")
        update = belief_processor.process(payload, short_memory, long_memory)
        memory.add(f"[ECHO_BELIEF_UPDATE] {json.dumps(update, ensure_ascii=True)}")
        return json.dumps(update, ensure_ascii=True)

    def get_state(self) -> entity_component.ComponentState:
        return {}

    def set_state(self, state: entity_component.ComponentState) -> None:
        del state


@dataclasses.dataclass(eq=False)
class EchoSocialMemoryStore(entity_component.ContextComponent):
    """Short/long memory state for loose social-media Echo agents."""

    topic: str = "Should we use euthanasia?"
    current_belief: int = 0
    current_opinion: str = ""
    with_long_memory: bool = True
    include_self_state: bool = True
    short_term_memory: str = ""
    long_term_memory: str = ""
    reasoning: str = ""

    def pre_observe(self, observation: str) -> str:
        text = str(observation or "").strip()
        if text:
            memory = self.get_entity().get_component(
                MEMORY_COMPONENT_KEY, type_=agent_components.memory.Memory
            )
            _add_tagged(memory, ECHO_OBSERVATION_TAG, text)
        return ""

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        del action_spec
        memory = self.get_entity().get_component(
            MEMORY_COMPONENT_KEY, type_=agent_components.memory.Memory
        )
        observations = _memories_since_last_long_memory(memory)
        observations_text = "\n".join(observations[-20:])
        if not observations_text:
            observations_text = "No new observations since the last belief update."
        self_state_lines = ""
        if self.include_self_state and self.with_long_memory:
            self_state_lines = (
                f"- previous belief: {self.current_belief}\n"
                f"- previous opinion: {self.current_opinion}\n"
            )
        return (
            "EchoChamber memory state:\n"
            f"- topic: {self.topic}\n"
            f"{self_state_lines}"
            f"- long-term memory: {self.long_term_memory or 'No long-term memory yet.'}\n"
            "Observations since the last belief update:\n"
            f"{observations_text}"
        )

    def get_state(self) -> entity_component.ComponentState:
        return {
            "topic": self.topic,
            "current_belief": self.current_belief,
            "current_opinion": self.current_opinion,
            "with_long_memory": self.with_long_memory,
            "include_self_state": self.include_self_state,
            "short_term_memory": self.short_term_memory,
            "long_term_memory": self.long_term_memory,
            "reasoning": self.reasoning,
        }

    def set_state(self, state: entity_component.ComponentState) -> None:
        self.topic = str(state.get("topic", self.topic) or self.topic)
        try:
            self.current_belief = int(
                state.get("current_belief", state.get("initial_belief", self.current_belief))
            )
        except (TypeError, ValueError):
            pass
        self.current_opinion = str(
            state.get("current_opinion", state.get("initial_opinion", self.current_opinion))
            or self.current_opinion
        )
        self.with_long_memory = bool(state.get("with_long_memory", self.with_long_memory))
        self.include_self_state = bool(state.get("include_self_state", self.include_self_state))
        self.short_term_memory = str(state.get("short_term_memory", "") or "")
        self.long_term_memory = str(state.get("long_term_memory", "") or "")
        self.reasoning = str(state.get("reasoning", "") or "")


class EchoSocialMemoryToolActComponent(SocialConcatActComponent):
    """Tool-calling act component with Echo short/long memory consolidation."""

    def _memory_store(self) -> EchoSocialMemoryStore:
        return self.get_entity().get_component(SOCIAL_MEMORY_STORE_KEY, type_=EchoSocialMemoryStore)

    def _memory(self) -> agent_components.memory.Memory:
        return self.get_entity().get_component(
            MEMORY_COMPONENT_KEY, type_=agent_components.memory.Memory
        )

    def _structured_call(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        if hasattr(self._model, "sample_structured_response"):
            parsed = self._model.sample_structured_response(prompt, schema)
            if isinstance(parsed, dict) and parsed:
                return parsed
        try:
            raw = self._model.sample_text(
                prompt
                + "\n\nReturn only one valid JSON object matching this schema:\n"
                + json.dumps(schema, ensure_ascii=True),
                max_tokens=1200,
                temperature=None,
                terminators=(),
            )
            parsed = extract_structured_response(str(raw))
            return parsed if isinstance(parsed, dict) and parsed else dict(fallback)
        except Exception:
            return dict(fallback)

    def _short_memory(self, observations_text: str) -> str:
        prompt = (
            f"The opinions and social-media events you have observed since your last "
            f"long-term memory update are:\n{observations_text}\n\n"
            "Task:\n"
            "Summarize the provided observations to form your short-term memory.\n\n"
            "Instructions:\n"
            "- Do not add or create information that is not present in the provided observations.\n"
            '- Start the summary with: "In my short-term memory, ..."\n'
            "- Provide a brief and accurate summary of the opinions and interactions you observed."
        )
        parsed = self._structured_call(
            prompt=prompt,
            schema={
                "title": "echo_short_memory",
                "type": "object",
                "properties": {"short_term_memory": {"type": "string"}},
                "required": ["short_term_memory"],
                "additionalProperties": False,
            },
            fallback={"short_term_memory": f"In my short-term memory, {observations_text}"},
        )
        return str(parsed.get("short_term_memory", "") or "")

    def _long_memory(self, store: EchoSocialMemoryStore, short_memory: str) -> str:
        if not store.with_long_memory:
            return store.long_term_memory
        prompt = (
            f"Recap of Previous Long-Term Memory: {store.long_term_memory}\n"
            f"Today's Short-Term Memory: {short_memory}\n\n"
            "Task:\n"
            "Using only the information in the previous long-term memory and today's short-term memory, "
            "create an updated long-term memory.\n\n"
            "Instructions:\n"
            "- Do not introduce any new information that is not present in the provided memories.\n"
            '- Start the updated memory with: "In my long-term memory, ..."\n'
            "- Accurately combine key details from both the long-term and short-term memories into a clear summary."
        )
        parsed = self._structured_call(
            prompt=prompt,
            schema={
                "title": "echo_long_memory",
                "type": "object",
                "properties": {"long_term_memory": {"type": "string"}},
                "required": ["long_term_memory"],
                "additionalProperties": False,
            },
            fallback={
                "long_term_memory": (
                    f"In my long-term memory, {store.long_term_memory} {short_memory}".strip()
                )
            },
        )
        return str(parsed.get("long_term_memory", "") or "")

    def _consolidate_memory(self, store: EchoSocialMemoryStore) -> None:
        memory = self._memory()
        observations = _memories_since_last_long_memory(memory)
        observations_text = "\n".join(observations)
        if not observations_text:
            observations_text = "No new observations since the last belief update."
        short_memory = self._short_memory(observations_text)
        store.short_term_memory = short_memory
        _add_tagged(memory, ECHO_SHORT_MEMORY_TAG, short_memory)

        long_memory = self._long_memory(store, short_memory)
        if long_memory:
            store.long_term_memory = long_memory
        _add_tagged(memory, ECHO_LONG_MEMORY_TAG, store.long_term_memory)

    def _belief_update_attempt(
        self,
        contexts: entity_component.ComponentContextMapping,
        action_spec: entity_lib.ActionSpec,
    ) -> str:
        store = self._memory_store()
        self._consolidate_memory(store)
        context = self._context_for_action(contexts)
        schema = {
            "title": "echo_opinion_belief_update",
            "type": "object",
            "properties": {
                "opinion": {"type": "string"},
                "belief": {"type": "integer", "minimum": -2, "maximum": 2},
                "reasoning": {"type": "string"},
            },
            "required": ["opinion", "belief", "reasoning"],
            "additionalProperties": False,
        }
        call_to_action, _ = self._extract_structured_schema(action_spec.call_to_action)
        question = (
            f"{call_to_action}\n\n"
            f"Your short-term memory: {store.short_term_memory}\n"
            f"Your long-term memory: {store.long_term_memory}\n"
            "Belief values: '-2' for strongly oppose, '-1' for somewhat oppose, "
            "'0' for neutral, '1' for somewhat support, '2' for strongly support.\n\n"
            "Task:\n"
            "Reflect on your opinion and belief, considering whether to maintain your stance "
            "or adjust it based on your long-term memory.\n\n"
            "Instructions:\n"
            "- Think like a human: decide whether to hold firm in your own opinion "
            "or adapt based on the influence of the opinions in your memory.\n"
            "- Provide your current opinion on the topic in several sentences.\n"
            "- Return exactly one JSON object."
        )
        payload = self._structured_call(
            prompt=f"{context}\n\n{question}",
            schema=schema,
            fallback={
                "opinion": store.current_opinion,
                "belief": store.current_belief,
                "reasoning": "No valid structured belief update was produced.",
            },
        )
        try:
            belief = int(payload.get("belief", store.current_belief))
        except (TypeError, ValueError):
            belief = store.current_belief
        belief = max(-2, min(2, belief))
        update = {
            "opinion": str(payload.get("opinion", store.current_opinion) or store.current_opinion),
            "belief": belief,
            "reasoning": str(payload.get("reasoning", "") or ""),
        }
        store.current_belief = belief
        store.current_opinion = update["opinion"]
        store.reasoning = update["reasoning"]
        _add_tagged(self._memory(), ECHO_BELIEF_UPDATE_TAG, json.dumps(update, ensure_ascii=True))
        result = json.dumps({"structured_response": update}, ensure_ascii=True)
        prompt_doc = interactive_document.InteractiveDocument(self._model)
        prompt_doc.statement(context + "\n")
        self._log(result, prompt_doc)
        return result

    def get_action_attempt(
        self,
        contexts: entity_component.ComponentContextMapping,
        action_spec: entity_lib.ActionSpec,
    ) -> str:
        if (
            action_spec.tag == "echo_belief_probe"
            and action_spec.output_type in entity_lib.FREE_ACTION_TYPES
        ):
            return self._belief_update_attempt(contexts, action_spec)
        return super().get_action_attempt(contexts, action_spec)


@dataclasses.dataclass
class Entity(prefab_lib.Prefab):
    """Componentized EchoChamberSim replication agent."""

    description: str = "EchoChamberSim Concordia-component replication agent"
    params: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank | list[str],
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        del memory_bank
        params = dict(self.params)
        agent_name = str(params.get("name", "Agent"))
        system_prompt = str(
            params.get("system_prompt") or params.get("context") or "Imagine you are a human."
        )
        temperature = params.get("temperature")
        llm_response_mode = str(params.get("llm_response_mode", "json_object"))
        prompt_variant = str(params.get("prompt_variant", "compat"))
        short_cls = _load_class(
            str(
                params.get("short_memory_processor_class")
                or "replications.echo_chambers.components.agent_concordia.ShortMemoryProcessor"
            ),
            EchoProcessor,
        )
        long_cls = _load_class(
            str(
                params.get("long_memory_processor_class")
                or "replications.echo_chambers.components.agent_concordia.LongMemoryProcessor"
            ),
            EchoProcessor,
        )
        belief_cls = _load_class(
            str(
                params.get("belief_update_processor_class")
                or "replications.echo_chambers.components.agent_concordia.BeliefUpdateProcessor"
            ),
            EchoProcessor,
        )

        memory_component = agent_components.memory.ListMemory(memory_bank=[])
        shared = {
            "model": model,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "llm_response_mode": llm_response_mode,
            "prompt_variant": prompt_variant,
        }
        components: dict[str, entity_component.ContextComponent] = {
            OBSERVATION_COMPONENT_KEY: EchoObservationPayload(),
            MEMORY_COMPONENT_KEY: memory_component,
            SHORT_MEMORY_PROCESSOR_KEY: short_cls(**shared),
            LONG_MEMORY_PROCESSOR_KEY: long_cls(**shared),
            BELIEF_UPDATE_PROCESSOR_KEY: belief_cls(**shared),
        }
        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=agent_name,
            act_component=EchoChamberActComponent(),
            context_components=components,
        )


@dataclasses.dataclass
class EchoSocialToolEntity(prefab_lib.Prefab):
    """Echo persona agent for loose twitter-like action studies.

    Unlike ``Entity`` above, this prefab acts through the normal twitter-like
    app tool interface.  It still preserves the EchoChamber short/long-memory
    cycle: observations accumulate as short-term input, memory consolidation is
    triggered by a private belief CHOICE action spec, and normal social actions
    do not themselves mutate the Echo memory state.
    """

    description: str = "EchoChamber social-media tool-calling agent"
    params: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank | list[str],
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        params = dict(self.params)
        agent_name = str(params.get("name", "Agent"))
        cfg = ConfigStore.get_config()

        instructions = agent_components.instructions.Instructions(agent_name=agent_name)
        instructions._state = cfg.sim.roleplaying_instructions.format(name=agent_name)

        if isinstance(memory_bank, list):
            memory_component: entity_component.ContextComponent = (
                agent_components.memory.ListMemory(memory_bank=memory_bank)
            )
        else:
            memory_component = agent_components.memory.AssociativeMemory(memory_bank=memory_bank)

        try:
            initial_belief_value = int(params.get("initial_belief", 0) or 0)
        except (TypeError, ValueError):
            initial_belief_value = 0

        topic = "Should we use euthanasia?"
        scenario_context = str(params.get("scenario_context", "") or "")
        if scenario_context.strip():
            topic = scenario_context.strip()

        components: dict[str, entity_component.ContextComponent] = {
            INSTRUCTIONS_COMPONENT_KEY: instructions,
            agent_components.memory.DEFAULT_MEMORY_COMPONENT_KEY: memory_component,
            SOCIAL_MEMORY_STORE_KEY: EchoSocialMemoryStore(
                topic=topic,
                current_belief=initial_belief_value,
                current_opinion=str(params.get("initial_opinion", "") or ""),
                with_long_memory=bool(getattr(cfg, "echo_with_long_memory", True)),
                include_self_state=bool(
                    params.get(
                        "include_self_state_feedback",
                        getattr(cfg, "echo_include_self_state_feedback", True),
                    )
                ),
            ),
        }
        component_order = [
            INSTRUCTIONS_COMPONENT_KEY,
            SOCIAL_MEMORY_STORE_KEY,
        ]

        context = str(params.get("context", "") or "")
        if context:
            components[PERSONA_INFORMATION_KEY] = agent_components.constant.Constant(
                state=context,
                pre_act_label="Information of Persona to Simulate",
            )
            component_order.insert(1, PERSONA_INFORMATION_KEY)

        if scenario_context:
            components[SCENARIO_CONTEXT_KEY] = agent_components.constant.Constant(
                state=scenario_context,
                pre_act_label="Scenario Information",
            )
            component_order.insert(1, SCENARIO_CONTEXT_KEY)

        initial_opinion = str(params.get("initial_opinion", "") or "")
        initial_belief = str(params.get("initial_belief", "") or "")
        if initial_opinion or initial_belief:
            stance = (
                "Initial stance measurement:\n"
                f"- belief: {initial_belief}\n"
                f"- opinion: {initial_opinion}"
            )
            components["__Initial Echo Stance__"] = agent_components.constant.Constant(
                state=stance,
                pre_act_label="Initial Echo Stance",
            )
            component_order.insert(1, "__Initial Echo Stance__")

        goal = str(params.get("goal", "") or "")
        if goal:
            components[DEFAULT_GOAL_COMPONENT_KEY] = agent_components.constant.Constant(
                state=goal,
                pre_act_label="Overarching goal",
            )
            component_order.insert(1, DEFAULT_GOAL_COMPONENT_KEY)

        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=agent_name,
            act_component=EchoSocialMemoryToolActComponent(
                model=model,
                component_order=component_order,
                randomize_choices=False,
            ),
            context_components=components,
        )
