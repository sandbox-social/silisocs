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
``temperature``, ``extra_kwargs``). Kwargs the factory does not declare are
dropped to its signature *except* the user-authored routing/pass-through fields
(:data:`STRICT_PROVIDER_FIELDS`): if one of those is set in ``sim.llm`` and the
provider cannot accept it, construction fails loudly rather than silently sending
requests to the wrong endpoint (or without the requested body fields). Providers
that talk to OpenAI-compatible HTTP APIs should subclass
:class:`OpenAICompatibleLanguageModel` to inherit retry/backoff and telemetry
support.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from silisocs.runtime.class_loading import instantiate_with_supported_kwargs

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


# ``sim.llm`` fields whose silent loss would change where a request goes, which
# model answers it, or what the request body contains. A provider that cannot accept
# one of these while it is actually set is a config error, not a filtered framework
# kwarg. ``log_file``/``debug``/``temperature`` stay filterable: they are supplied by
# the framework on every call (with defaults), so a provider may legitimately ignore
# them.
STRICT_PROVIDER_FIELDS: tuple[str, ...] = ("model_name", "api_base", "api_key", "extra_kwargs")


def instantiate_provider(
    factory: Callable[..., Any], kwargs: dict[str, Any], *, provider: str = ""
) -> Any:
    """Call a provider factory with the kwargs its signature accepts.

    Framework kwargs the factory does not declare are dropped; a *set*
    :data:`STRICT_PROVIDER_FIELDS` entry it cannot accept raises a config error
    naming the provider and the offending field(s).
    """
    strict = {field for field in STRICT_PROVIDER_FIELDS if kwargs.get(field)}
    return instantiate_with_supported_kwargs(
        factory,
        kwargs,
        strict_keys=strict,
        config_path=f"sim.llm (provider '{provider}')" if provider else "sim.llm",
    )
