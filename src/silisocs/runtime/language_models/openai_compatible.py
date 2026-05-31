"""OpenAI-compatible language-model provider."""

import os
from typing import Any

from silisocs.runtime.language_models.openai import (
    DEFAULT_STATS_CHANNEL,
    Measurements,
    OpenAILanguageModel,
)


class OpenAICompatibleLanguageModel(OpenAILanguageModel):
    """Language model for OpenAI-compatible endpoints such as vLLM or Ollama."""

    def __init__(
        self,
        model_name: str,
        *,
        api_base: str,
        api_key: str | None = None,
        temperature: float = 0.5,
        measurements: Measurements | None = None,
        channel: str = DEFAULT_STATS_CHANNEL,
        log_file: str = "prompts_and_outputs.jsonl",
        debug: bool | None = True,
        extra_kwargs: dict[str, Any] | None = None,
    ) -> None:
        if not str(api_base or "").strip():
            raise ValueError("OpenAICompatibleLanguageModel requires api_base.")
        super().__init__(
            model_name=model_name,
            api_key=api_key or os.getenv("OPENAI_API_KEY") or "openai-compatible-api-key",
            api_base=api_base,
            temperature=temperature,
            measurements=measurements,
            channel=channel,
            log_file=log_file,
            debug=debug,
            extra_kwargs=extra_kwargs,
        )
