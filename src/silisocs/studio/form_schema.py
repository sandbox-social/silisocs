"""The declarative composer framework: fields, schemas, and capability registries.

This layer is deliberately content-free and dependency-light — it imports no
engine, backend, or analysis code, so a router can import it at module scope
without paying for the composer's import tree. The silisocs-specific schema and
its choice/preview providers live in :mod:`silisocs.studio.form_providers`.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

from silisocs.runtime.class_loading import load_class


@dataclass(frozen=True)
class Field:
    key: str
    widget: str
    label: str
    group: str
    help: str = ""
    advanced: bool = False
    choices: tuple[str, ...] = ()
    choices_from: str | None = None
    choices_depend_on: tuple[str, ...] = ()
    # Show this field only while other fields hold given values, e.g.
    # ``{"env.gm.backend.type": "custom"}``. A list accepts any of several
    # values (``{"env.gm.backend.type": ["backend_a", "backend_b"]}``),
    # which is how a field scopes itself to a family of backends.
    visible_when: dict[str, Any] | None = None
    widget_params: dict[str, Any] = dataclass_field(default_factory=dict)
    preview_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormSchema:
    name: str
    version: int
    fields: tuple[Field, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass(frozen=True)
class PreviewContext:
    """Workspace context supplied to on-demand form preview providers."""

    repository_root: Path


@dataclass(frozen=True)
class ChoiceContext:
    """Workspace capabilities supplied to dynamic choice providers."""

    repository_root: Path
    extension_options: Callable[[str], Sequence[Mapping[str, str]]] | None = None

    def extensions(self, catalog: str) -> tuple[str, ...]:
        return tuple(item["value"] for item in self.options(catalog))

    def options(self, catalog: str) -> tuple[dict[str, str], ...]:
        if self.extension_options is None:
            return ()
        return tuple(
            {"value": str(item["value"]), "label": str(item["label"])}
            for item in self.extension_options(catalog)
        )


_SCHEMAS: dict[str, FormSchema] = {}
ChoiceProvider = Callable[..., Sequence[str]]
_CHOICE_PROVIDERS: dict[str, ChoiceProvider] = {}
_DEFERRED_CHOICE_PROVIDERS: set[str] = set()
PreviewProvider = Callable[[Mapping[str, str], str, PreviewContext], Any]
_PREVIEW_PROVIDERS: dict[str, PreviewProvider] = {}


def register_form_schema(schema: FormSchema) -> None:
    if not schema.name:
        raise ValueError("FormSchema.name must be non-empty")
    _SCHEMAS[schema.name] = schema


def form_schema(name: str = "scenario") -> FormSchema:
    try:
        return _SCHEMAS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown form schema {name!r}") from exc


def list_form_schemas() -> list[FormSchema]:
    """Return registered composer schemas in stable name order."""
    return [_SCHEMAS[name] for name in sorted(_SCHEMAS)]


def list_choice_providers() -> tuple[str, ...]:
    """Return registered dynamic-choice capability names."""
    return tuple(sorted(_CHOICE_PROVIDERS))


def list_preview_providers() -> tuple[str, ...]:
    """Return registered on-demand preview capability names."""
    return tuple(sorted(_PREVIEW_PROVIDERS))


def register_choice_provider(
    name: str, provider: ChoiceProvider, *, deferred: bool = False
) -> None:
    """Register a dynamic form-choice provider by stable capability name."""
    key = str(name).strip()
    if not key or not callable(provider):
        raise ValueError("Choice providers require a name and callable")
    _CHOICE_PROVIDERS[key] = provider
    if deferred:
        _DEFERRED_CHOICE_PROVIDERS.add(key)
    else:
        _DEFERRED_CHOICE_PROVIDERS.discard(key)


def choice_provider_is_deferred(name: str) -> bool:
    """Whether a provider is expensive enough that the first render skips it."""
    return name in _DEFERRED_CHOICE_PROVIDERS


def run_choice_provider(
    name: str,
    files: Mapping[str, str],
    context: ChoiceContext | None = None,
) -> list[str]:
    """Resolve one registered choice capability from the current documents."""
    try:
        provider = _CHOICE_PROVIDERS[name]
    except KeyError as exc:
        # Only the silisocs provider layer knows the runtime built-in catalogs a
        # ``component.*``/``policy.*`` name may still resolve against, and it is
        # the expensive half of the composer — reached here only on a miss.
        from silisocs.studio.form_providers import (  # noqa: PLC0415
            runtime_catalog_provider_for,
        )

        fallback = runtime_catalog_provider_for(name)
        if fallback is None:
            raise KeyError(f"Unknown choice provider {name!r}") from exc
        provider = fallback
        register_choice_provider(name, provider, deferred=True)
    parameters = inspect.signature(provider).parameters
    choices = provider(files, context) if len(parameters) >= 2 else provider(files)
    return [str(choice) for choice in choices]


def choice_items(
    name: str,
    choices: Sequence[str],
    context: ChoiceContext | None = None,
) -> list[dict[str, str]]:
    """Project raw choices into labels without changing submitted runtime values."""
    if not choices:
        return []
    options = context.options(name) if context else ()
    if not options and name.endswith(".classes") and context:
        options = context.options(name.removesuffix(".classes"))
    labels = {item["value"]: item["label"] for item in options}
    return [{"value": choice, "label": labels.get(choice, choice)} for choice in choices]


def register_preview_provider(name: str, provider: PreviewProvider) -> None:
    """Register an on-demand form preview capability."""
    key = str(name).strip()
    if not key or not callable(provider):
        raise ValueError("Preview providers require a name and callable")
    _PREVIEW_PROVIDERS[key] = provider


def run_preview_provider(
    name: str,
    files: Mapping[str, str],
    item_key: str,
    context: PreviewContext,
) -> Any:
    """Run one registered preview provider without accepting import paths over HTTP."""
    try:
        provider = _PREVIEW_PROVIDERS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown preview provider {name!r}") from exc
    return provider(files, item_key, context)


def load_widget(class_path: str) -> type[Any]:
    """Resolve a custom composer widget class through the standard class-path seam."""
    return load_class(class_path, what="composer widget class")
