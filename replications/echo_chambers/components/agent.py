"""Agent runtime for the EchoChamberSim replication."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from pydantic import BaseModel

from replications.echo_chambers.components.app import extract_observation
from silisocs.agents.base_agent import Agent
from silisocs.runtime import language_models as language_model


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


def _json_prompt(task: str, system_prompt: str, user_prompt: str, schema_hint: str) -> str:
    return (
        f"{system_prompt.strip()}\n\n"
        f"{task.strip()}\n\n"
        f"{user_prompt.strip()}\n\n"
        "Return only a valid JSON object. Do not wrap it in Markdown.\n"
        f"Required JSON schema: {schema_hint}"
    )


class UpdateOpinionResponse(BaseModel):
    opinion: str
    belief: int
    reasoning: str


class ReflectingResponse(BaseModel):
    short_term_memory: str


class LongMemoryResponse(BaseModel):
    long_term_memory: str


class EchoChamberAgentRuntime(Agent):
    """LLM agent that reproduces EchoChamberSim's memory/update pipeline."""

    def __init__(
        self,
        *,
        model: language_model.LanguageModel,
        **params: Any,
    ) -> None:
        super().__init__(model)
        self._params = dict(params)
        self._agent_name = str(params.get("name", "Agent"))
        self._system_prompt = str(
            params.get("system_prompt") or params.get("context") or "Imagine you are a human."
        )
        self._last_observation = ""
        self._last_payload: dict[str, Any] | None = None
        self._temperature = params.get("temperature")
        self._llm_response_mode = str(params.get("llm_response_mode", "json_object"))
        self._prompt_variant = str(params.get("prompt_variant", "compat"))

    @property
    def name(self) -> str:
        return self._agent_name

    def observe(self, observation: str) -> None:
        self._last_observation = str(observation or "")
        self._last_payload = extract_observation(self._last_observation)

    def _sample_json(
        self,
        *,
        task: str,
        user_prompt: str,
        schema_hint: str,
        response_type: type[BaseModel] | None = None,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        # Smoke tests often run with Concordia's no-op language model.  In that
        # case, keep the simulation structurally valid without pretending to have
        # new language evidence.
        json_instruction = (
            f"{task.strip()}\n\n"
            f"{user_prompt.strip()}\n\n"
            "Return only a valid JSON object. Do not wrap it in Markdown.\n"
            f"Required JSON schema: {schema_hint}"
        )
        client = getattr(self._model, "_client", None)
        model_name = getattr(self._model, "_model_name", None)
        if client is not None and model_name:
            max_retries = int(getattr(self._model, "_max_retries", 2))
            for attempt in range(max_retries + 1):
                try:
                    use_structured_parse = (
                        response_type is not None
                        and self._llm_response_mode in {"structured_parse", "parse"}
                    )
                    messages = [
                        {"role": "system", "content": self._system_prompt},
                        {
                            "role": "user",
                            "content": user_prompt if use_structured_parse else json_instruction,
                        },
                    ]
                    temperature = (
                        self._temperature
                        if self._temperature is not None
                        else getattr(self._model, "_temperature", 1.0)
                    )
                    if use_structured_parse:
                        response = client.beta.chat.completions.parse(
                            model=model_name,
                            messages=messages,
                            temperature=temperature,
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
                            temperature=temperature,
                            max_tokens=900,
                            timeout=120,
                            response_format={"type": "json_object"},
                        )
                        raw = response.choices[0].message.content or ""
                        parsed = _extract_json_object(raw)
                    recorder = getattr(self._model, "_record_retry_outcome", None)
                    if callable(recorder):
                        recorder(attempt, True)
                    logger = getattr(self._model, "_log", None)
                    if callable(logger):
                        logger(
                            f"SYSTEM:\n{self._system_prompt}\n\nUSER:\n{messages[1]['content']}",
                            raw,
                        )
                    return parsed if parsed is not None else dict(fallback)
                except Exception:
                    if attempt >= max_retries:
                        recorder = getattr(self._model, "_record_retry_outcome", None)
                        if callable(recorder):
                            recorder(attempt, False)
                        return dict(fallback)
                    time.sleep(min(4.0, 1.0 + attempt))

        prompt = _json_prompt(task, self._system_prompt, user_prompt, schema_hint)
        for _ in range(3):
            try:
                raw = self._model.sample_text(
                    prompt,
                    max_tokens=900,
                    temperature=self._temperature,
                    terminators=None,
                )
            except Exception:
                return dict(fallback)
            parsed = _extract_json_object(raw)
            if parsed is not None:
                return parsed
        return dict(fallback)

    def _short_memory(self, payload: dict[str, Any]) -> str:
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
        if not opinions_text and self._prompt_variant == "compat":
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
        if self._prompt_variant == "exact":
            user_prompt += (
                "\n\nOutput:\n- short_term_memory: Your summarized short-term memory statement."
            )
        parsed = self._sample_json(
            task="Short-term memory reflection",
            user_prompt=user_prompt,
            schema_hint='{"short_term_memory": "string"}',
            response_type=ReflectingResponse,
            fallback={"short_term_memory": f"In my short-term memory, {opinions_text}"},
        )
        return str(parsed.get("short_term_memory", "") or "")

    def _long_memory(self, payload: dict[str, Any], short_memory: str) -> str:
        if not bool(payload.get("with_long_memory", True)):
            return str(payload.get("long_memory", "") or "")
        user_prompt = (
            f"Recap of Previous Long-Term Memory: {payload.get('long_memory', '')}\n"
            f"Today's Short-Term Memory: {short_memory}\n\n"
            "Task:\n"
            "Using only the information in the previous long-term memory and today's short-term memory, "
            "create an updated long-term memory.\n\n"
            "Instructions:\n"
            "- Do not introduce any new information that is not present in the provided memories.\n"
            '- Start the updated memory with: "In my long-term memory, ..."\n'
            "- Accurately combine key details from both the long-term and short-term memories into a clear summary."
        )
        if self._prompt_variant == "exact":
            user_prompt += (
                "\n\nOutput:\n"
                "- long_term_memory: Your new, consolidated long-term memory statement."
            )
        parsed = self._sample_json(
            task="Long-term memory consolidation",
            user_prompt=user_prompt,
            schema_hint='{"long_term_memory": "string"}',
            response_type=LongMemoryResponse,
            fallback={
                "long_term_memory": (
                    f"In my long-term memory, {payload.get('long_memory', '')} {short_memory}"
                ).strip()
            },
        )
        return str(parsed.get("long_term_memory", "") or "")

    def _update_opinion(
        self,
        payload: dict[str, Any],
        *,
        short_memory: str,
        long_memory: str,
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
            f"{'Output structure (in code format):' if self._prompt_variant == 'exact' else ''}\n\n"
            f"opinion: Provide your current opinion on the topic '{payload.get('topic', '')}' "
            f"in several sentences. Your opinion must contain one keyword from "
            f"{payload.get('belief_keywords', {})} that reflects your stance. It should begin with: "
            '"I {the selected keyword}"\n\n'
            "belief: Indicate your current belief value regarding the topic.\n\n"
            "reasoning: Explain the reasoning behind your opinion and belief, elaborating on "
            "whether you upheld your original stance or were influenced by the opinions in your long-term memory."
        )
        parsed = self._sample_json(
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
            "agent_name": self._agent_name,
            "agent_id": int(payload.get("agent_id", -1) or -1),
            "belief": new_belief,
            "opinion": str(parsed.get("opinion", payload.get("opinion", "")) or ""),
            "reasoning": str(parsed.get("reasoning", "") or ""),
            "short_term_memory": short_memory,
            "long_term_memory": long_memory,
            "contact_ids": list(payload.get("contact_ids", []) or []),
        }

    def act(self, action_spec: Any) -> str:
        del action_spec
        payload = self._last_payload
        if payload is None:
            payload = {
                "episode": 0,
                "agent_name": self._agent_name,
                "agent_id": -1,
                "belief": int(self._params.get("initial_belief", 0) or 0),
                "opinion": str(self._params.get("initial_opinion", "") or ""),
                "long_memory": "",
                "belief_keywords": {},
                "topic": "Should we use euthanasia?",
                "opinions_heard": [],
                "passive_perspectives": [],
                "contact_ids": [],
                "with_long_memory": True,
            }
        short_memory = self._short_memory(payload)
        long_memory = self._long_memory(payload, short_memory)
        update = self._update_opinion(
            payload,
            short_memory=short_memory,
            long_memory=long_memory,
        )
        return json.dumps(update, ensure_ascii=True)

    def get_state(self) -> dict[str, Any]:
        return {
            "last_observation": self._last_observation,
            "last_payload": self._last_payload,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        self._last_observation = str(state.get("last_observation", "") or "")
        payload = state.get("last_payload")
        self._last_payload = payload if isinstance(payload, dict) else None
