import json
import os
import random
import threading
import time
from collections import deque
from collections.abc import Collection, Sequence

import httpx
import openai
from concordia.language_model import language_model, no_language_model
from concordia.utils import measurements as measurements_lib
from concordia.utils import sampling

from mastodon_sim.utils.misc import write_jsonl_item

_MAX_MULTIPLE_CHOICE_ATTEMPTS = 20
_DEFAULT_MAX_RETRIES = 50
_DEFAULT_BACKOFF_BASE_SECONDS = 5
_DEFAULT_BACKOFF_MAX_SECONDS = 30.0
_RETRY_HISTORY_WINDOW = 500


class GptLanguageModel(language_model.LanguageModel):
    """Language Model that uses OpenAI GPT models."""

    def __init__(
        self,
        model_name: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        measurements: measurements_lib.Measurements | None = None,
        channel: str = language_model.DEFAULT_STATS_CHANNEL,
        log_file: str = "prompts_and_outputs.jsonl",
        debug: bool | None = True,
    ):
        """Initializes the instance.

        Args:
          model_name: The language model to use. For more details, see
            https://platform.openai.com/docs/guides/text-generation/which-model-should-i-use.
          api_key: The API key to use when accessing the OpenAI API. If None, will
            use the OPENAI_API_KEY environment variable.
          measurements: The measurements object to log usage statistics to.
          channel: The channel to write the statistics to.
        """
        if api_key is None:
            api_key = os.environ["OPENAI_API_KEY"]
        self._api_key = api_key
        self._model_name = model_name
        self._measurements = measurements
        self._channel = channel
        # Check if model is qwen3.5 to determine if extra_body should be used
        self._use_qwen_extra_body = "qwen" in model_name.lower() and "3.5" in model_name.lower()
        if api_base:
            self._client = openai.OpenAI(
                api_key=self._api_key,
                base_url=api_base,
                http_client=httpx.Client(
                    limits=httpx.Limits(
                        max_connections=1024,
                        max_keepalive_connections=256,
                    ),
                    timeout=httpx.Timeout(timeout=180.0, connect=30.0),
                ),
            )
        else:
            self._client = openai.OpenAI(
                api_key=self._api_key,
                http_client=httpx.Client(
                    limits=httpx.Limits(
                        max_connections=1024,
                        max_keepalive_connections=256,
                    ),
                    timeout=httpx.Timeout(timeout=180.0, connect=30.0),
                ),
            )
        self._log_file = log_file
        self.debug = debug
        self.meta_data = {"episode_idx": -1, "agent_name": ""}
        self.agent_names: list[str] = []
        self._agent_name_index: dict[str, str] = {}  # prefix -> full name
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
        # Phase-scoped cumulative counters (lightweight; no rolling windows).
        self._phase_counters: dict[str, dict[str, int]] = {
            "probe": {"calls": 0, "failed": 0, "retries": 0},
            "action": {"calls": 0, "failed": 0, "retries": 0},
        }

    def _log(self, prompt: str, output: str):
        # Use thread-local agent_name if set by caller, else look up from index.
        agent_name = getattr(self._local, "agent_name", None) if hasattr(self, "_local") else None
        if not agent_name:
            prefix = prompt[:110]
            for token_len in (30, 50, 80, 110):
                agent_name = self._agent_name_index.get(prefix[:token_len])
                if agent_name:
                    break
            if not agent_name:
                agent_name = "not found"
        self.meta_data["agent_name"] = agent_name
        log_entry = {"prompt": prompt, "output": output} | self.meta_data
        try:
            write_jsonl_item(log_entry, self._log_file)
        except Exception as e:
            print(f"Logging error: {e}")

    def _rebuild_agent_name_index(self) -> None:
        """Rebuild prefix->name index when agent_names changes."""
        index: dict[str, str] = {}
        for name in self.agent_names:
            for length in (30, 50, 80, 110):
                # The prompt typically starts with the agent name, so short
                # prefixes of the prompt can be used as keys.
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
        """Tag subsequent retry accounting with the current engine phase."""
        normalized = str(phase).strip().lower()
        if normalized not in {"probe", "action", "other"}:
            normalized = "other"
        with self._retry_stats_lock:
            self._retry_phase = normalized

    def get_retry_counters(self, phase: str = "all") -> dict[str, int]:
        """Return cumulative retry counters since model initialization.

        `phase` can be one of: `all`, `probe`, `action`.
        """
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
        max_tokens: int = language_model.DEFAULT_MAX_TOKENS,
        terminators: Collection[str] | None = language_model.DEFAULT_TERMINATORS,
        temperature: float = 0.5,
        timeout: float = language_model.DEFAULT_TIMEOUT_SECONDS,
        media: Sequence[str] | None = None,
        seed: int | None = 0,
    ) -> str:
        max_tokens = min(max_tokens, 4000)

        messages: list[dict[str, str | dict[str, str]]] = [
            {
                "role": "system",
                "content": (
                    "You always continue sentences provided "
                    "by the user and you never repeat what "
                    "the user already said."
                ),
            },
            {"role": "user", "content": "Question: Is Jake a turtle?\nAnswer: Jake is "},
            {"role": "assistant", "content": "not a turtle."},
            {
                "role": "user",
                "content": (
                    "Question: What is Priya doing right now?\nAnswer: " + "Priya is currently "
                ),
            },
            {"role": "assistant", "content": "sleeping."},
        ]

        if media:
            content: list[dict[str, str | dict[str, str]]] = [{"type": "text", "text": prompt}]

            for url in media:
                content.append({"type": "image_url", "image_url": {"url": url}})

            messages.append({"role": "user", "content": content})  # type: ignore
            stop_param = None  # Ensure stop parameter is not passed if media is provided
        else:
            messages.append({"role": "user", "content": prompt})
            stop_param = terminators

        response = None
        for attempt in range(self._max_retries + 1):
            try:
                # Build kwargs conditionally: only add extra_body for qwen3.5
                kwargs = {
                    "model": self._model_name,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                    "seed": seed,
                }
                if self._use_qwen_extra_body:
                    kwargs["extra_body"] = {
                        "top_k": 20,
                        "chat_template_kwargs": {"enable_thinking": False},
                    }
                if stop_param is not None:
                    kwargs["stop"] = stop_param

                response = self._client.chat.completions.create(**kwargs)  # type: ignore
                self._record_retry_outcome(attempt, success=True)
                break
            except openai.APIError as e:
                print(f"OpenAI API returned an API Error (attempt {attempt + 1}): {e}")
            except openai.APIConnectionError as e:
                print(f"Failed to connect to OpenAI API (attempt {attempt + 1}): {e}")
            except openai.RateLimitError as e:
                print(f"OpenAI API request exceeded rate limit (attempt {attempt + 1}): {e}")

            if attempt >= self._max_retries:
                self._record_retry_outcome(attempt, success=False)
                raise RuntimeError(
                    "LLM call failed after bounded retries. "
                    f"max_retries={self._max_retries}, model={self._model_name}"
                )

            # Exponential backoff with jitter to reduce synchronized retry storms.
            sleep_seconds = min(
                self._backoff_base_seconds * (2**attempt),
                self._backoff_max_seconds,
            )
            sleep_seconds += random.uniform(0, self._backoff_base_seconds)
            time.sleep(sleep_seconds)

        if response is None:
            self._record_retry_outcome(self._max_retries, success=False)
            raise RuntimeError("LLM call did not produce a response.")

        if self._measurements is not None:
            answer = response.choices[0].message.content
            raw_text_length = len(answer) if answer else 0
            self._measurements.publish_datum(self._channel, {"raw_text_length": raw_text_length})

        answer = response.choices[0].message.content
        if answer is None:
            raise ValueError("Response content is None.")
        if self.debug:
            self._log(prompt, answer)
        return answer

    def sample_choice(
        self,
        prompt: str,
        responses: Sequence[str],
        *,
        seed: int | None = None,
    ) -> tuple[int, str, dict[str, float]]:
        prompt = (
            prompt
            + "\nRespond EXACTLY with one of the following strings:\n"
            + "\n".join(responses)
            + "."
        )

        sample = ""
        answer = ""
        for attempts in range(_MAX_MULTIPLE_CHOICE_ATTEMPTS):
            temperature = sampling.dynamically_adjust_temperature(
                attempts, _MAX_MULTIPLE_CHOICE_ATTEMPTS
            )

            sample = self.sample_text(
                prompt,
                temperature=temperature,
                seed=seed,
            )
            answer = sampling.extract_choice_response(sample)
            try:
                idx = responses.index(answer)
            except ValueError:
                continue
            else:
                if self._measurements is not None:
                    self._measurements.publish_datum(self._channel, {"choices_calls": attempts})
                debug: dict[str, float] = {}
                return idx, responses[idx], debug

        raise language_model.InvalidResponseError(
            f"Too many multiple choice attempts.\nLast attempt: {sample}, extracted: {answer}"
        )

    def sample_tool_call(
        self,
        prompt: str,
        tools: list[dict],
    ) -> tuple[str, dict]:
        """Call the LLM with OpenAI-compatible tool schemas and return the invoked function.

        Args:
            prompt: The user-facing prompt describing the task.
            tools: A list of OpenAI tool schema dicts (as produced by
                ``PhoneApp.generate_tool_schemas()``).

        Returns
        -------
            A ``(function_name, args_dict)`` tuple.  Returns ``("", {})`` if the
            model does not invoke any tool or if all retry attempts fail.
        """
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
                # Build kwargs conditionally: only add extra_body for qwen3.5
                kwargs = {
                    "model": self._model_name,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "required",
                    "temperature": 0.5,
                    "timeout": 60,
                }
                if self._use_qwen_extra_body:
                    kwargs["extra_body"] = {
                        "top_k": 20,
                        "chat_template_kwargs": {"enable_thinking": False},
                    }

                response = self._client.chat.completions.create(**kwargs)  # type: ignore[call-overload]
                self._record_retry_outcome(attempt, success=True)
                msg = response.choices[0].message
                if msg.tool_calls:
                    tc = msg.tool_calls[0]
                    args = json.loads(tc.function.arguments)
                    if self.debug:
                        self._log(prompt, f"tool_call:{tc.function.name}({args})")
                    return tc.function.name, args
                return "", {}
            except openai.APIError as e:
                print(f"Tool call API error (attempt {attempt + 1}): {e}")
            except openai.APIConnectionError as e:
                print(f"Tool call connection error (attempt {attempt + 1}): {e}")
            except openai.RateLimitError as e:
                print(f"Tool call rate limit (attempt {attempt + 1}): {e}")
            except Exception as e:
                print(f"Tool call unexpected error (attempt {attempt + 1}): {e}")

            if attempt >= self._max_retries:
                self._record_retry_outcome(attempt, success=False)
                return "", {}

            sleep_seconds = min(
                self._backoff_base_seconds * (2**attempt),
                self._backoff_max_seconds,
            )
            sleep_seconds += random.uniform(0, self._backoff_base_seconds)
            time.sleep(sleep_seconds)

        return "", {}


def select_large_language_model(
    model_name,
    log_file,
    debug_mode,
    disable_language_model=False,
    api_base: str | None = None,
    api_key: str | None = None,
):
    if disable_language_model:
        model = no_language_model.NoLanguageModel()
    elif "sonnet" in model_name:
        raise ValueError(
            "Model names containing 'sonnet' are not configured in this runtime. "
            "Use a supported OpenAI-compatible model name (e.g. 'gpt-*' or 'qwen-*')."
        )
    elif api_base:
        # Any OpenAI-compatible server (e.g., vLLM) can be used when api_base is set.
        effective_api_key = api_key or os.getenv("OPENAI_API_KEY") or "local-api-key"
        model = GptLanguageModel(
            api_key=effective_api_key,
            model_name=model_name,
            api_base=api_base,
            log_file=log_file,
            debug=debug_mode,
        )
    elif "gpt" in model_name:
        gpt_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not gpt_api_key:
            raise ValueError("GPT_API_KEY is required.")
        model = GptLanguageModel(
            api_key=gpt_api_key, model_name=model_name, log_file=log_file, debug=debug_mode
        )
    elif "qwen" in model_name:
        # Backward-compatible local default for qwen when no explicit endpoint is set.
        model = GptLanguageModel(
            api_key=api_key or "abcd",
            model_name=model_name,
            api_base="http://localhost:30000/v1",
            log_file=log_file,
            debug=debug_mode,
        )
    else:
        raise ValueError("Unknown model name.")
    return model
