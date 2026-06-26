"""Language-model provider selection."""

import importlib
import os
from typing import Any

from silisocs.runtime.language_models.base import LanguageModel, NoLanguageModel
from silisocs.runtime.language_models.openai import OpenAILanguageModel
from silisocs.runtime.language_models.openai_compatible import OpenAICompatibleLanguageModel
from silisocs.runtime.language_models.registry import (
    available_llm_providers,
    get_llm_provider,
    instantiate_provider,
)
from silisocs.runtime.language_models.scripted import ScriptedLanguageModel

BUILT_IN_PROVIDERS = ("openai", "openai_compatible", "scripted", "disabled")

# Common OpenAI-compatible endpoints. Each maps a provider name to
# (base_url, api_key_env_var_or_None). An explicit ``sim.llm.api_base`` overrides
# the preset base URL; providers with an env var require a key (via the env var
# or ``sim.llm.api_key``), while keyless presets (e.g. local Ollama) need none.
OPENAI_COMPATIBLE_PRESETS: dict[str, tuple[str, str | None]] = {
    "anthropic": ("https://api.anthropic.com/v1/", "ANTHROPIC_API_KEY"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
    ),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "fireworks": ("https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),  # local, no key required
}


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
        raise ValueError(
            "sim.llm.provider is required: openai | openai_compatible | scripted | disabled."
        )

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
    if normalized_provider == "openai_compatible":
        return OpenAICompatibleLanguageModel(
            api_key=api_key,
            model_name=model_name,
            api_base=str(api_base or ""),
            temperature=temperature,
            log_file=log_file,
            debug=debug_mode,
            extra_kwargs=extra_kwargs,
        )

    if normalized_provider in OPENAI_COMPATIBLE_PRESETS:
        preset_base, key_env = OPENAI_COMPATIBLE_PRESETS[normalized_provider]
        effective_api_key = api_key or (os.getenv(key_env) if key_env else None)
        if key_env and not effective_api_key:
            raise ValueError(
                f"{key_env} or sim.llm.api_key is required for provider={normalized_provider}."
            )
        return OpenAICompatibleLanguageModel(
            model_name=model_name,
            api_base=(str(api_base or "").strip() or preset_base),
            api_key=effective_api_key,
            temperature=temperature,
            log_file=log_file,
            debug=debug_mode,
            extra_kwargs=extra_kwargs,
        )

    custom_factory = get_llm_provider(normalized_provider)
    if custom_factory is None and "." in str(provider or ""):
        module_path, _, attr = str(provider).strip().rpartition(".")
        try:
            custom_factory = getattr(importlib.import_module(module_path), attr)
        except (ImportError, AttributeError) as exc:
            raise ValueError(
                f"sim.llm.provider '{provider}' looks like a class path but cannot "
                f"be imported: {exc}"
            ) from exc
    if custom_factory is not None:
        built = instantiate_provider(
            custom_factory,
            {
                "model_name": model_name,
                "log_file": log_file,
                "debug": debug_mode,
                "api_base": api_base,
                "api_key": api_key,
                "temperature": temperature,
                "extra_kwargs": extra_kwargs,
            },
        )
        if not isinstance(built, LanguageModel):
            raise TypeError(
                f"Custom provider '{provider}' returned {type(built).__name__}, "
                "not a silisocs LanguageModel."
            )
        return built

    known = ", ".join([*BUILT_IN_PROVIDERS, *OPENAI_COMPATIBLE_PRESETS, *available_llm_providers()])
    raise ValueError(
        f"Unknown sim.llm.provider '{provider}'. Available: {known}. "
        "Custom providers: register with @register_llm_provider or use a "
        "fully qualified class path."
    )
