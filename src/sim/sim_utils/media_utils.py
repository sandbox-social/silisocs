import json
import os
from collections.abc import Collection, Sequence

import openai
import portalocker
from concordia.language_model import language_model, no_language_model
from concordia.utils import measurements as measurements_lib
from concordia.utils import sampling

_MAX_MULTIPLE_CHOICE_ATTEMPTS = 20


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
        if api_base:
            self._client = openai.OpenAI(api_key=self._api_key, base_url=api_base)
        else:
            self._client = openai.OpenAI(api_key=self._api_key)
        self._log_file = log_file
        self.debug = debug
        self.meta_data = {"episode_idx": -1, "agent_name": ""}
        self.agent_names: list[str] = []

    def _log(self, prompt: str, output: str):  ## Function for logging
        agent_name = "not found"
        for test_agent_name in self.agent_names:
            if test_agent_name in prompt[:110]:
                agent_name = test_agent_name
        self.meta_data["agent_name"] = agent_name
        log_entry = {"prompt": prompt, "output": output} | self.meta_data
        try:
            with open(self._log_file, "a") as f:  # Use "a" mode (append)
                portalocker.lock(f, portalocker.LOCK_EX)  # Acquire an exclusive lock
                f.write(json.dumps(log_entry) + "\n")
                f.flush()
        except Exception as e:
            print(f"Logging error: {e}")
        finally:
            if not f.closed:
                portalocker.unlock(f)  # Ensure the lock is always released

    def sample_text(
        self,
        prompt: str,
        *,
        max_tokens: int = language_model.DEFAULT_MAX_TOKENS,
        terminators: Collection[str] | None = language_model.DEFAULT_TERMINATORS,
        temperature: float = 0,
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

        has_result = False
        while not has_result:
            try:
                response = self._client.chat.completions.create(  # type: ignore
                    model=self._model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    seed=seed,
                    **({"stop": stop_param} if stop_param is not None else {}),
                )
                has_result = True
            except openai.APIError as e:
                # Handle API error here, e.g. retry or log
                print(f"OpenAI API returned an API Error: {e}")
                print(prompt)
            except openai.APIConnectionError as e:
                # Handle connection error here
                print(f"Failed to connect to OpenAI API: {e}")
            except openai.RateLimitError as e:
                # Handle rate limit error (we recommend using exponential backoff)
                print(f"OpenAI API request exceeded rate limit: {e}")

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


def select_large_language_model(model_name, log_file, debug_mode, disable_language_model=False):
    if disable_language_model:
        model = no_language_model.NoLanguageModel()
    elif "sonnet" in model_name:
        GPT_API_KEY = os.getenv("ANTHROPIC_API_KEY")
        model = amazon_bedrock_model.AmazonBedrockLanguageModel(
            # -            model_id="anthropic.claude-3-5-sonnet-20240620-v1:0"
            model_id="claude-3-5-sonnet-latest"  # "anthropic.claude-3-5-sonnet-20240620-v1:0"
        )

    elif "gpt" in model_name:
        GPT_API_KEY = os.getenv("OPENAI_API_KEY")
        if not GPT_API_KEY:
            raise ValueError("GPT_API_KEY is required.")
        model = GptLanguageModel(
            api_key=GPT_API_KEY, model_name=model_name, log_file=log_file, debug=debug_mode
        )
    elif "Qwen" in model_name:
        model = model = GptLanguageModel(
            api_key="abcd",
            model_name=model_name,
            api_base="http://localhost:8000/v1",
            log_file=log_file,
            debug=debug_mode,
        )
    else:
        raise ValueError("Unknown model name.")
    return model
