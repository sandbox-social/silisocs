"""Regression tests: an explicitly registered provider (or dotted class path)
takes precedence over a built-in OpenAI-compatible preset of the same name.

See the review fix in ``silisocs.runtime.language_models.factory``: registry /
class-path resolution must run *before* the preset table so a user-registered
provider whose name collides with a preset (e.g. ``ollama``, ``groq``) is not
silently shadowed by the built-in preset.
"""

from __future__ import annotations

from typing import Any

import pytest

from silisocs.runtime.language_models import (
    OpenAICompatibleLanguageModel,
    select_large_language_model,
)
from silisocs.runtime.language_models.base import LanguageModel


class _RegisteredOllamaModel(LanguageModel):
    """Minimal custom provider used to override the 'ollama' preset name."""

    def __init__(self, *, model_name: str = "", temperature: float = 0.0) -> None:
        self.model_name = model_name
        self.temperature = temperature

    def sample_text(self, prompt: str, **kwargs: Any) -> str:  # type: ignore[override]
        return "registered"


def _register(monkeypatch: pytest.MonkeyPatch, name: str, factory: Any) -> None:
    """Register ``factory`` under ``name`` without leaking global state.

    ``register_llm_provider`` mutates the module-level ``_PROVIDERS`` dict; we
    patch the same dict object in place so monkeypatch restores it on teardown.
    """
    from silisocs.runtime.language_models import registry

    patched = dict(registry._PROVIDERS)
    patched[name.strip().lower()] = factory
    monkeypatch.setattr(registry, "_PROVIDERS", patched)


def _normalized_base_url(model: OpenAICompatibleLanguageModel) -> str:
    return str(model._client.base_url).rstrip("/")  # type: ignore[attr-defined]


def test_registered_provider_overrides_colliding_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A provider registered under a preset name ('ollama') must win.
    _register(monkeypatch, "ollama", _RegisteredOllamaModel)

    model = select_large_language_model(
        model_name="my-model",
        log_file="",
        debug_mode=False,
        provider="ollama",
        temperature=0.7,
    )

    assert isinstance(model, _RegisteredOllamaModel)
    assert not isinstance(model, OpenAICompatibleLanguageModel)
    assert model.model_name == "my-model"
    assert model.temperature == 0.7


def test_preset_still_used_when_no_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without an override, 'ollama' resolves to the built-in keyless preset.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    model = select_large_language_model(
        model_name="m",
        log_file="p.jsonl",
        debug_mode=False,
        provider="ollama",
    )

    assert isinstance(model, OpenAICompatibleLanguageModel)
    assert _normalized_base_url(model) == "http://localhost:11434/v1"


def test_normal_preset_resolves_to_openai_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A standard preset (groq, with a key) still builds the preset model.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    model = select_large_language_model(
        model_name="m",
        log_file="p.jsonl",
        debug_mode=False,
        provider="groq",
        api_key="test-key",
    )

    assert isinstance(model, OpenAICompatibleLanguageModel)
    assert _normalized_base_url(model) == "https://api.groq.com/openai/v1"


def test_reserved_core_names_remain_non_shadowable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even if someone registers under a reserved core name, the built-in wins.
    from silisocs.runtime.language_models.base import NoLanguageModel
    from silisocs.runtime.language_models.scripted import ScriptedLanguageModel

    _register(monkeypatch, "disabled", _RegisteredOllamaModel)
    model = select_large_language_model(
        model_name="m",
        log_file="",
        debug_mode=False,
        provider="disabled",
    )
    assert isinstance(model, NoLanguageModel)

    _register(monkeypatch, "scripted", _RegisteredOllamaModel)
    scripted = select_large_language_model(
        model_name="m",
        log_file="",
        debug_mode=False,
        provider="scripted",
    )
    assert isinstance(scripted, ScriptedLanguageModel)
