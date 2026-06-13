"""Registry for custom language-model providers.

Two extension paths exist for `sim.llm.provider`:

1. **Registry** — decorate a factory (class or function returning a
   :class:`~silisocs.runtime.language_models.base.LanguageModel`) and import
   the module before the runner builds models::

       from silisocs.runtime.language_models.registry import register_llm_provider

       @register_llm_provider("my_provider")
       class MyModel(LanguageModel): ...

   then set ``sim.llm.provider: my_provider``.

2. **Dotted path** — set ``sim.llm.provider: mypkg.models.MyModel`` directly;
   the factory imports and instantiates it without registration.

Either way the factory is called with the standard provider kwargs
(``model_name``, ``log_file``, ``debug``, ``api_base``, ``api_key``,
``temperature``, ``extra_kwargs``); unsupported kwargs are dropped to the
factory's signature. Providers that talk to OpenAI-compatible HTTP APIs should
subclass :class:`OpenAICompatibleLanguageModel` to inherit retry/backoff and
telemetry support.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_PROVIDERS: dict[str, Callable[..., Any]] = {}


def register_llm_provider(name: str) -> Callable[[F], F]:
    """Register a language-model provider factory under ``name``."""
    normalized = str(name).strip().lower()
    if not normalized:
        raise ValueError("Provider name must be non-empty.")

    def _decorator(factory: F) -> F:
        existing = _PROVIDERS.get(normalized)
        if existing is not None and existing is not factory:
            raise ValueError(f"LLM provider '{normalized}' is already registered.")
        _PROVIDERS[normalized] = factory
        return factory

    return _decorator


def get_llm_provider(name: str) -> Callable[..., Any] | None:
    """Return the registered factory for ``name`` (case-insensitive), if any."""
    return _PROVIDERS.get(str(name).strip().lower())


def available_llm_providers() -> list[str]:
    """Return sorted names of registered (non-built-in) providers."""
    return sorted(_PROVIDERS)


def instantiate_provider(factory: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    """Call a provider factory with only the kwargs its signature accepts."""
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return factory(**kwargs)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return factory(**kwargs)
    supported = {
        name
        for name, param in params.items()
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return factory(**{key: value for key, value in kwargs.items() if key in supported})
