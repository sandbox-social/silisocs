"""Public language-model surface with provider implementations loaded on demand."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from silisocs.runtime.language_models.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TERMINATORS,
    DEFAULT_TIMEOUT_SECONDS,
    InvalidResponseError,
    LanguageModel,
    NoLanguageModel,
)
from silisocs.runtime.language_models.catalog import OPENAI_COMPATIBLE_PRESETS

_LAZY_EXPORTS = {
    "Measurements": ("silisocs.runtime.language_models.openai", "Measurements"),
    "OpenAILanguageModel": (
        "silisocs.runtime.language_models.openai",
        "OpenAILanguageModel",
    ),
    "OpenAICompatibleLanguageModel": (
        "silisocs.runtime.language_models.openai_compatible",
        "OpenAICompatibleLanguageModel",
    ),
    "ScriptedLanguageModel": (
        "silisocs.runtime.language_models.scripted",
        "ScriptedLanguageModel",
    ),
    "select_large_language_model": (
        "silisocs.runtime.language_models.factory",
        "select_large_language_model",
    ),
}


def __getattr__(name: str) -> Any:
    """Load optional provider implementations when their public symbol is requested."""
    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TERMINATORS",
    "DEFAULT_TIMEOUT_SECONDS",
    "OPENAI_COMPATIBLE_PRESETS",
    "InvalidResponseError",
    "LanguageModel",
    "Measurements",
    "NoLanguageModel",
    "OpenAICompatibleLanguageModel",
    "OpenAILanguageModel",
    "ScriptedLanguageModel",
    "select_large_language_model",
]
