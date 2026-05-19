"""OpenAI chat-completions language-model provider."""

import json
import os
import random
import threading
import time
from collections import deque
from collections.abc import Collection, Mapping, Sequence
from typing import Any, cast

import httpx
import openai

from silisocs.runtime.io import write_jsonl_item
from silisocs.runtime.language_models.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TERMINATORS,
    DEFAULT_TIMEOUT_SECONDS,
    InvalidResponseError,
    LanguageModel,
    extract_choice_response,
)
from silisocs.runtime.types import ToolCall

_MAX_MULTIPLE_CHOICE_ATTEMPTS = 20
_DEFAULT_MAX_RETRIES = 50
_DEFAULT_BACKOFF_BASE_SECONDS = 5
_DEFAULT_BACKOFF_MAX_SECONDS = 30.0
_RETRY_HISTORY_WINDOW = 500
DEFAULT_STATS_CHANNEL = "language_model_stats"


class Measurements:
    """Small stand-in for usage metric publication."""

    def publish_datum(self, channel: str, datum: Mapping[str, Any]) -> None:
        del channel, datum


def dynamically_adjust_temperature(attempt: int, max_attempts: int) -> float:
    if max_attempts <= 1:
        return 0.0
    return min(1.5, 0.1 + (attempt / max_attempts))


class OpenAILanguageModel(LanguageModel):
    """Language model backed by the official OpenAI chat-completions API."""

    def __init__(
        self,
        model_name: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        temperature: float = 0.5,
        measurements: Measurements | None = None,
        channel: str = DEFAULT_STATS_CHANNEL,
        log_file: str = "prompts_and_outputs.jsonl",
        debug: bool | None = True,
        extra_kwargs: dict[str, Any] | None = None,
    ):
        if api_key is None:
            api_key = os.environ["OPENAI_API_KEY"]
        self._api_key = api_key
        self._model_name = model_name
        self._temperature = temperature
        self._measurements = measurements
        self._channel = channel
        self._extra_kwargs = dict(extra_kwargs or {})
        client_kwargs = {
            "api_key": self._api_key,
            "http_client": httpx.Client(
                limits=httpx.Limits(max_connections=1024, max_keepalive_connections=256),
                timeout=httpx.Timeout(timeout=180.0, connect=30.0),
            ),
        }
        if api_base:
            client_kwargs["base_url"] = api_base
        self._client = cast(Any, openai.OpenAI)(**client_kwargs)
        self._log_file = log_file
        self.debug = debug
        self.meta_data = {"episode_idx": -1, "agent_name": "", "phase": "", "tag": ""}
        self.agent_names: list[str] = []
        self._agent_name_index: dict[str, str] = {}
        self._local = threading.local()
        self._max_retries = int(os.getenv("SIM_LLM_MAX_RETRIES", _DEFAULT_MAX_RETRIES))
        self._backoff_base_seconds = float(
            os.getenv("SIM_LLM_BACKOFF_BASE_SECONDS", _DEFAULT_BACKOFF_BASE_SECONDS)
        )
        self._backoff_max_seconds = float(
            os.getenv("SIM_LLM_BACKOFF_MAX_SECONDS", _DEFAULT_BACKOFF_MAX_SECONDS)
        )
        self._retry_stats_lock = threading.Lock()
        self._retry_history: deque[int] = deque(maxlen=_RETRY_HISTORY_WINDOW)
        self._failure_history: deque[int] = deque(maxlen=_RETRY_HISTORY_WINDOW)
        self._calls_total = 0
        self._failed_calls_total = 0
        self._retries_total = 0
        self._retry_phase = "other"
        self._phase_counters: dict[str, dict[str, int]] = {
            "probe": {"calls": 0, "failed": 0, "retries": 0},
            "action": {"calls": 0, "failed": 0, "retries": 0},
        }

    def _request_kwargs(self, **kwargs: Any) -> dict[str, Any]:
        merged = dict(kwargs)
        merged.update(self._extra_kwargs)
        return merged

    def _log(self, prompt: str, output: str):
        if not self.debug:
            return
        agent_name = getattr(self._local, "agent_name", None) if hasattr(self, "_local") else None
        if not agent_name:
            prefix = prompt[:110]
            index = getattr(self, "_agent_name_index", {})
            for token_len in (30, 50, 80, 110):
                agent_name = index.get(prefix[:token_len])
                if agent_name:
                    break
            if not agent_name:
                agent_name = "not found"
        episode_idx = getattr(self._local, "episode_idx", self.meta_data.get("episode_idx", -1))
        phase = getattr(self._local, "phase", self.meta_data.get("phase", ""))
        action_tag = getattr(self._local, "tag", self.meta_data.get("tag", ""))
        self.meta_data["agent_name"] = str(agent_name)
        self.meta_data["episode_idx"] = _coerce_int(episode_idx, default=-1)
        self.meta_data["phase"] = str(phase or "")
        self.meta_data["tag"] = str(action_tag or "")
        write_jsonl_item({"prompt": prompt, "output": output} | self.meta_data, self._log_file)

    def set_runtime_context(
        self,
        *,
        agent_name: str | None = None,
        episode_idx: int | None = None,
        phase: str | None = None,
        action_tag: str | None = None,
    ) -> None:
        if agent_name is not None:
            self._local.agent_name = str(agent_name)
        if episode_idx is not None:
            self._local.episode_idx = int(episode_idx)
            self.meta_data["episode_idx"] = int(episode_idx)
        if phase is not None:
            self._local.phase = str(phase)
        if action_tag is not None:
            self._local.tag = str(action_tag)

    def clear_runtime_context(self) -> None:
        self._local = threading.local()

    def _rebuild_agent_name_index(self) -> None:
        index: dict[str, str] = {}
        for name in self.agent_names:
            for length in (30, 50, 80, 110):
                key = name[:length] if len(name) >= length else name
                index.setdefault(key, name)
        self._agent_name_index = index

    def _record_retry_outcome(self, retries: int, success: bool) -> None:
        with self._retry_stats_lock:
            self._retry_history.append(retries)
            self._failure_history.append(0 if success else 1)
            self._calls_total += 1
            self._failed_calls_total += 0 if success else 1
            self._retries_total += retries
            phase_ctr = self._phase_counters.get(self._retry_phase)
            if phase_ctr is not None:
                phase_ctr["calls"] += 1
                phase_ctr["failed"] += 0 if success else 1
                phase_ctr["retries"] += retries

    def set_retry_phase(self, phase: str) -> None:
        normalized = str(phase).strip().lower()
        if normalized not in {"probe", "action", "other"}:
            normalized = "other"
        with self._retry_stats_lock:
            self._retry_phase = normalized

    def get_retry_counters(self, phase: str = "all") -> dict[str, int]:
        normalized = str(phase).strip().lower()
        with self._retry_stats_lock:
            phase_ctr = self._phase_counters.get(normalized)
            if phase_ctr is not None:
                return {
                    "calls_total": phase_ctr["calls"],
                    "failed_calls_total": phase_ctr["failed"],
                    "retries_total": phase_ctr["retries"],
                }
            return {
                "calls_total": self._calls_total,
                "failed_calls_total": self._failed_calls_total,
                "retries_total": self._retries_total,
            }

    def sample_text(
        self,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        terminators: Collection[str] | None = DEFAULT_TERMINATORS,
        temperature: float | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        media: Sequence[str] | None = None,
        seed: int | None = 0,
    ) -> str:
        if temperature is None:
            temperature = self._temperature
        max_tokens = min(max_tokens, 4000)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "You are a helpful, instruction-following assistant."}
        ]
        if media:
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            content.extend({"type": "image_url", "image_url": {"url": url}} for url in media)
            messages.append({"role": "user", "content": content})
            stop_param = None
        else:
            messages.append({"role": "user", "content": prompt})
            stop_param = terminators

        response = None
        for attempt in range(self._max_retries + 1):
            try:
                kwargs = self._request_kwargs(
                    model=self._model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    seed=seed,
                )
                if stop_param is not None:
                    kwargs["stop"] = stop_param
                response = cast(Any, self._client.chat.completions.create)(**kwargs)
                self._record_retry_outcome(attempt, success=True)
                break
            except openai.APIError as e:
                print(f"OpenAI API returned an API Error (attempt {attempt + 1}): {e}")
            except openai.APIConnectionError as e:
                print(f"Failed to connect to OpenAI API (attempt {attempt + 1}): {e}")
            except openai.RateLimitError as e:
                print(f"OpenAI API request exceeded rate limit (attempt {attempt + 1}): {e}")
            self._sleep_or_fail(attempt, "LLM call")

        if response is None:
            self._record_retry_outcome(self._max_retries, success=False)
            raise RuntimeError("LLM call did not produce a response.")
        answer = response.choices[0].message.content
        if answer is None:
            raise ValueError("Response content is None.")
        if self._measurements is not None:
            self._measurements.publish_datum(self._channel, {"raw_text_length": len(answer)})
        if self.debug:
            self._log(prompt, answer)
        return answer

    def sample_choice(
        self,
        prompt: str,
        responses: Sequence[str],
        *,
        seed: int | None = None,
        **kwargs: Any,
    ) -> tuple[int, str, dict[str, float]]:
        del kwargs
        prompt = (
            prompt
            + "\nRespond EXACTLY with one of the following strings:\n"
            + "\n".join(responses)
            + "."
        )
        sample = ""
        answer = ""
        for attempts in range(_MAX_MULTIPLE_CHOICE_ATTEMPTS):
            temperature = dynamically_adjust_temperature(attempts, _MAX_MULTIPLE_CHOICE_ATTEMPTS)
            sample = self.sample_text(prompt, temperature=temperature, seed=seed)
            answer = extract_choice_response(sample)
            try:
                idx = responses.index(answer)
            except ValueError:
                continue
            if self._measurements is not None:
                self._measurements.publish_datum(self._channel, {"choices_calls": attempts})
            return idx, responses[idx], {}
        raise InvalidResponseError(
            f"Too many multiple choice attempts.\nLast attempt: {sample}, extracted: {answer}"
        )

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        mode: str | None = None,
        **kwargs: Any,
    ) -> list[ToolCall]:
        tool_mode = str(mode or self._resolve_tool_calling_mode()).strip().lower()
        if tool_mode not in {"single", "multi"}:
            tool_mode = "single"
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant. "
                    "Call the single most appropriate function to complete the user's request."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        for attempt in range(self._max_retries + 1):
            try:
                request_kwargs = self._request_kwargs(
                    model=self._model_name,
                    messages=messages,
                    tools=tools,
                    tool_choice="required",
                    temperature=0.5,
                    timeout=60,
                    **kwargs,
                )
                response = cast(Any, self._client.chat.completions.create)(**request_kwargs)
                self._record_retry_outcome(attempt, success=True)
                msg = response.choices[0].message
                if not msg.tool_calls:
                    raise ValueError("Model returned no tool calls.")
                parsed_calls = [
                    call
                    for call in (_parse_tool_call(tc) for tc in msg.tool_calls)
                    if call is not None
                ]
                if tool_mode == "single":
                    parsed_calls = parsed_calls[:1]
                self._log(
                    prompt,
                    "tool_calls:"
                    + ", ".join(f"{call.name}({dict(call.arguments)})" for call in parsed_calls),
                )
                return parsed_calls
            except openai.APIError as e:
                print(f"Tool call API error (attempt {attempt + 1}): {e}")
            except openai.APIConnectionError as e:
                print(f"Tool call connection error (attempt {attempt + 1}): {e}")
            except openai.RateLimitError as e:
                print(f"Tool call rate limit (attempt {attempt + 1}): {e}")
            except Exception as e:
                print(f"Tool call unexpected error (attempt {attempt + 1}): {e}")
            self._sleep_or_fail(attempt, "Tool call")
        raise RuntimeError("Tool call did not produce a response.")

    def sample_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        max_tokens: int = 1200,
        temperature: float | None = None,
        **extra_request_kwargs: Any,
    ) -> dict[str, Any]:
        if temperature is None:
            temperature = self._temperature
        safe_schema_name = _safe_schema_name(str(schema.get("title") or "structured_response"))
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant. Return exactly one JSON object "
                    "matching the requested schema and no surrounding text."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        for attempt in range(self._max_retries + 1):
            try:
                kwargs = self._request_kwargs(
                    model=self._model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=min(max_tokens, 4000),
                    timeout=120,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": safe_schema_name,
                            "schema": schema,
                            "strict": False,
                        },
                    },
                    **extra_request_kwargs,
                )
                response = cast(Any, self._client.chat.completions.create)(**kwargs)
                parsed = _parse_json_content(response.choices[0].message.content)
                self._record_retry_outcome(attempt, success=True)
                self._log(prompt, json.dumps(parsed, ensure_ascii=True))
                return parsed
            except openai.BadRequestError as e:
                if attempt == 0:
                    fallback = self._sample_structured_json_object(
                        messages, temperature, max_tokens
                    )
                    if fallback is not None:
                        self._record_retry_outcome(attempt, success=True)
                        self._log(prompt, json.dumps(fallback, ensure_ascii=True))
                        return fallback
                print(f"Structured response API error (attempt {attempt + 1}): {e}")
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                print(f"Structured response parse error (attempt {attempt + 1}): {e}")
            except openai.APIError as e:
                print(f"Structured response API error (attempt {attempt + 1}): {e}")
            except openai.APIConnectionError as e:
                print(f"Structured response connection error (attempt {attempt + 1}): {e}")
            except openai.RateLimitError as e:
                print(f"Structured response rate limit (attempt {attempt + 1}): {e}")
            except Exception as e:
                print(f"Structured response unexpected error (attempt {attempt + 1}): {e}")
            self._sleep_or_fail(attempt, "Structured output")
        raise RuntimeError("Structured output did not produce a response.")

    def _sample_structured_json_object(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any] | None:
        try:
            kwargs = {
                "model": self._model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": min(max_tokens, 4000),
                "timeout": 120,
                "response_format": {"type": "json_object"},
            }
            response = cast(Any, self._client.chat.completions.create)(**kwargs)
            return _parse_json_content(response.choices[0].message.content)
        except Exception:
            return None

    def _sleep_or_fail(self, attempt: int, label: str) -> None:
        if attempt >= self._max_retries:
            self._record_retry_outcome(attempt, success=False)
            raise RuntimeError(
                f"{label} failed after bounded retries. "
                f"max_retries={self._max_retries}, model={self._model_name}"
            )
        sleep_seconds = min(
            self._backoff_base_seconds * (2**attempt),
            self._backoff_max_seconds,
        )
        sleep_seconds += random.uniform(0, self._backoff_base_seconds)
        time.sleep(sleep_seconds)

    @staticmethod
    def _resolve_tool_calling_mode() -> str:
        return "single"


def _parse_tool_call(tool_call: Any) -> ToolCall | None:
    tool_name = str(tool_call.function.name or "").strip()
    if not tool_name:
        return None
    try:
        raw_args: Any = json.loads(tool_call.function.arguments)
    except (TypeError, ValueError, json.JSONDecodeError):
        raw_args = {}
    return ToolCall(tool_name, raw_args if isinstance(raw_args, dict) else {})


def _parse_json_content(content: str | None) -> dict[str, Any]:
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").strip()
        raw = raw.removesuffix("```").strip()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Structured response was not a JSON object.")
    return parsed


def _safe_schema_name(schema_name: str) -> str:
    return (
        "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in schema_name)[:64]
        or "structured_response"
    )


def _coerce_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
