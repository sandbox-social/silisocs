"""Language-model provider selection."""

import os
from typing import Any

from silisocs.runtime.language_models.base import LanguageModel, NoLanguageModel
from silisocs.runtime.language_models.local import LocalLanguageModel
from silisocs.runtime.language_models.openai import OpenAILanguageModel
from silisocs.runtime.language_models.scripted import ScriptedLanguageModel


def select_large_language_model(
    model_name: str,
    log_file: str,
    debug_mode: bool,
    disable_language_model: bool = False,
    api_base: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.5,
    provider: str | None = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> LanguageModel:
    normalized_provider = str(provider or "").strip().lower()
    if disable_language_model:
        normalized_provider = "disabled"
    if not normalized_provider:
        raise ValueError("sim.llm.provider is required: openai | local | scripted | disabled.")
    if normalized_provider == "disabled":
        return NoLanguageModel()
    if normalized_provider == "scripted":
        return ScriptedLanguageModel(
            log_file=log_file, debug=debug_mode, **dict(extra_kwargs or {})
        )
    if normalized_provider == "openai":
        effective_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not effective_api_key:
            raise ValueError("OPENAI_API_KEY or sim.llm.api_key is required for provider=openai.")
        return OpenAILanguageModel(
            api_key=effective_api_key,
            model_name=model_name,
            temperature=temperature,
            log_file=log_file,
            debug=debug_mode,
            extra_kwargs=extra_kwargs,
        )
    if normalized_provider == "local":
        return LocalLanguageModel(
            api_key=api_key,
            model_name=model_name,
            api_base=str(api_base or ""),
            temperature=temperature,
            log_file=log_file,
            debug=debug_mode,
            extra_kwargs=extra_kwargs,
        )
    raise ValueError(
        f"Unknown sim.llm.provider '{provider}'. Available: openai, local, scripted, disabled."
    )
