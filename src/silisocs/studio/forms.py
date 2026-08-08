"""Declarative composer schemas, preflight, and YAML document composition."""

from __future__ import annotations

import importlib
import inspect
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from silisocs.simulation_engines.policies.factory import (
    build_participation_policy,
    build_turn_policy,
)
from silisocs.studio.scenario_repository import (
    ScenarioRepository as ScenarioRepository,  # noqa: PLC0414  (import-path compatibility)
)
from silisocs.studio.scenario_repository import (
    ensure_package_directive,
    leading_comment_block,
)

if TYPE_CHECKING:  # Type-only: the backend layer stays a lazy import at runtime.
    from silisocs.environments.backends.base import BackendApp


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


def run_choice_provider(
    name: str,
    files: Mapping[str, str],
    context: ChoiceContext | None = None,
) -> list[str]:
    """Resolve one registered choice capability from the current documents."""
    try:
        provider = _CHOICE_PROVIDERS[name]
    except KeyError as exc:
        classes = name.endswith(".classes")
        catalog = name.removesuffix(".classes")
        if catalog.startswith(("component.", "policy.")) and catalog in _runtime_built_in_catalog():
            provider = _runtime_catalog_provider(catalog, classes=classes)
            register_choice_provider(name, provider, deferred=True)
        else:
            raise KeyError(f"Unknown choice provider {name!r}") from exc
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


def materialize_form_schema(
    files: Mapping[str, str],
    name: str = "scenario",
    *,
    defer_expensive: bool = False,
    choice_context: ChoiceContext | None = None,
) -> dict[str, Any]:
    """Resolve dynamic field choices for one YAML document set."""
    schema = form_schema(name)
    values = field_values(dict(files), schema)
    fields = []
    for field in schema.fields:
        item = field.to_dict()
        if field.choices_from:
            deferred = defer_expensive and field.choices_from in _DEFERRED_CHOICE_PROVIDERS
            item["choices_deferred"] = deferred
            choices = (
                [] if deferred else run_choice_provider(field.choices_from, files, choice_context)
            )
            item["choices"] = choices
            item["choice_items"] = choice_items(
                field.choices_from,
                choices,
                choice_context,
            )
        else:
            item["choice_items"] = [{"value": choice, "label": choice} for choice in field.choices]
        if field.widget.startswith("class_path:"):
            widget = load_widget(field.widget.removeprefix("class_path:"))()
            render = getattr(widget, "render", None)
            if not callable(render):
                raise TypeError(
                    f"Composer widget {type(widget).__module__}.{type(widget).__name__} "
                    "must implement render(field, value, files)"
                )
            item["html"] = str(render(field=field, value=values.get(field.key), files=files))
        fields.append(item)
    return {"name": schema.name, "version": schema.version, "fields": fields}


def load_widget(class_path: str) -> type[Any]:
    """Resolve a custom composer widget class through the standard class-path seam."""
    module, name = class_path.rsplit(".", 1)
    widget = getattr(importlib.import_module(module), name)
    if not isinstance(widget, type):
        raise TypeError(f"{class_path!r} is not a widget class")
    return widget


register_form_schema(
    FormSchema(
        name="scenario",
        version=1,
        fields=(
            Field("world.scenario_name", "text", "Scenario name", "Basics"),
            Field("world.num_agents", "number", "Agents", "Basics"),
            Field("world.num_steps", "number", "Steps", "Basics"),
            Field("world.seed", "number", "Seed", "Basics"),
            Field("world.setting.name", "text", "Setting", "World"),
            Field("world.setting.background", "list[text]", "Background", "World"),
            Field("world.event.name", "text", "Event", "World"),
            Field("world.event.context", "yaml", "Event context", "World"),
            Field(
                "agents.persona_pipeline.classes",
                "agent_classes",
                "Agent classes",
                "Agents",
                help="Each key is a class id; each value is its declarative class configuration",
                choices_from="agent.classes",
                preview_from="persona.records",
            ),
            Field(
                "agents.builder.class_path",
                "combobox",
                "Agent builder",
                "Advanced",
                advanced=True,
                choices_from="agent.builders",
                help="Leave empty for the persona-pipeline builder or enter a custom class path",
            ),
            Field(
                "env.gm.backend.type",
                "select",
                "Backend",
                "Platform",
                choices_from="backend.types",
            ),
            Field(
                "env.gm.backend.class_path",
                "combobox",
                "Custom backend class",
                "Platform",
                choices_from="backend.classes",
                help="Fully qualified BackendApp subclass",
                visible_when={"env.gm.backend.type": "custom"},
            ),
            Field(
                "env.gm.backend.enabled_actions",
                "chips",
                "Enabled actions",
                "Platform",
                choices_from="backend.actions",
                choices_depend_on=(
                    "env.gm.backend.type",
                    "env.gm.backend.class_path",
                ),
            ),
            Field(
                "sim.llm.provider",
                "select",
                "Provider",
                "Model",
                choices_from="llm.providers",
            ),
            Field("sim.llm.name", "text", "Model", "Model"),
            Field("sim.llm.temperature", "number", "Temperature", "Model"),
            Field(
                "sim.engine.executor",
                "select",
                "Executor",
                "Advanced",
                advanced=True,
                choices=("threads", "asyncio"),
            ),
            Field(
                "sim.engine.loop.built_in",
                "select",
                "Loop strategy",
                "Advanced",
                advanced=True,
                choices_from="policy.loop",
            ),
            Field(
                "sim.engine.loop.class_path",
                "combobox",
                "Custom loop strategy",
                "Advanced",
                advanced=True,
                choices_from="policy.loop.classes",
            ),
            Field(
                "sim.engine.step.built_in",
                "select",
                "Step strategy",
                "Advanced",
                advanced=True,
                choices_from="policy.step",
            ),
            Field(
                "sim.engine.step.class_path",
                "combobox",
                "Custom step strategy",
                "Advanced",
                advanced=True,
                choices_from="policy.step.classes",
            ),
            Field(
                "sim.engine.turn_policy.built_in",
                "select",
                "Turn policy",
                "Advanced",
                advanced=True,
                choices_from="policy.turn",
            ),
            Field(
                "sim.engine.turn_policy.class_path",
                "combobox",
                "Custom turn policy",
                "Advanced",
                advanced=True,
                choices_from="policy.turn.classes",
            ),
            Field(
                "sim.engine.participation.built_in",
                "select",
                "Participation",
                "Advanced",
                advanced=True,
                choices_from="policy.participation",
            ),
            Field(
                "sim.engine.participation.class_path",
                "combobox",
                "Custom participation policy",
                "Advanced",
                advanced=True,
                choices_from="policy.participation.classes",
            ),
            Field(
                "env.gm.class_path",
                "combobox",
                "Game master",
                "Advanced",
                advanced=True,
                choices_from="game_master.classes",
            ),
            *(
                Field(
                    f"env.gm.components.{role}.built_in",
                    "select",
                    f"{role.replace('_', ' ').title()} component",
                    "Advanced",
                    advanced=True,
                    choices_from=f"component.{role}",
                )
                for role in (
                    "initialize",
                    "next_acting",
                    "update",
                    "observe",
                    "resolve",
                    "action_prompt",
                )
            ),
            *(
                Field(
                    f"env.gm.components.{role}.class_path",
                    "combobox",
                    f"Custom {role.replace('_', ' ')} component",
                    "Advanced",
                    advanced=True,
                    choices_from=f"component.{role}.classes",
                )
                for role in (
                    "initialize",
                    "next_acting",
                    "update",
                    "observe",
                    "resolve",
                    "action_prompt",
                )
            ),
            Field(
                "env.gm_orchestration",
                "yaml",
                "Game-master orchestration",
                "Advanced",
                advanced=True,
            ),
            Field("eval.probes.enabled", "toggle", "Enable probes", "Evaluation"),
            Field(
                "eval.probes.schedule.built_in",
                "select",
                "Probe schedule",
                "Evaluation",
                choices_from="policy.probe",
            ),
            Field(
                "eval.probes.schedule.class_path",
                "combobox",
                "Custom probe schedule",
                "Advanced",
                advanced=True,
                choices_from="policy.probe.classes",
            ),
            Field(
                "eval.probes.deployment",
                "yaml",
                "Probe deployment",
                "Evaluation",
            ),
            Field(
                "eval.probes.probes",
                "mapping",
                "Probe questions",
                "Evaluation",
                help="Each key is a probe id; each value declares question, type, and overrides",
            ),
        ),
    )
)


def _backend_types(_: Mapping[str, str]) -> Sequence[str]:
    from silisocs.environments.backends.factory import registered_backend_types

    return (*registered_backend_types(), "custom")


def _llm_providers(_: Mapping[str, str]) -> Sequence[str]:
    from silisocs.runtime.language_models.catalog import (
        BUILT_IN_PROVIDERS,
        OPENAI_COMPATIBLE_PRESETS,
    )
    from silisocs.runtime.language_models.registry import available_llm_providers

    return tuple(
        dict.fromkeys((*BUILT_IN_PROVIDERS, *OPENAI_COMPATIBLE_PRESETS, *available_llm_providers()))
    )


def _extension_choices(
    context: ChoiceContext | None,
    catalog: str,
    *,
    classes: bool,
) -> tuple[str, ...]:
    values = context.extensions(catalog) if context else ()
    return tuple(value for value in values if ("." in value) is classes)


def _catalog_provider(
    catalog: str,
    built_ins: Sequence[str] = (),
    *,
    classes: bool = False,
) -> ChoiceProvider:
    def provider(_: Mapping[str, str], context: ChoiceContext | None = None) -> Sequence[str]:
        return tuple(
            dict.fromkeys(
                (
                    *built_ins,
                    *_extension_choices(context, catalog, classes=classes),
                )
            )
        )

    return provider


def _configured_backend(files: Mapping[str, str]) -> tuple[str, type[BackendApp] | None]:
    """The backend a draft scenario selects: its type, and its class if importable."""
    from silisocs.environments.backends.factory import resolve_backend_class

    env = yaml.safe_load(files.get("env.yaml", "{}")) or {}
    backend = ((env.get("gm") or {}).get("backend") or {}) if isinstance(env, dict) else {}
    backend_type = str(backend.get("type") or "")
    try:
        cls = resolve_backend_class(
            backend_type,
            class_path=str(backend.get("class_path") or "") or None,
        )
    except (ImportError, AttributeError, TypeError, ValueError):
        return backend_type, None
    return backend_type, cls


def _backend_actions(files: Mapping[str, str]) -> Sequence[str]:
    _, cls = _configured_backend(files)
    if cls is None:
        return ()
    return tuple(item["selectable_name"] for item in cls.declared_action_catalog())


def _panel_catalog(files: Mapping[str, str]) -> Sequence[str]:
    """Run panels the drafted backend can actually feed.

    The same capability declarations that gate a finished run's views gate the
    composer, so you cannot build a view out of panels your scenario's backend
    will never populate.
    """
    from silisocs.analysis.panel import list_panels
    from silisocs.studio.capabilities import backend_panel_names

    backend_type, cls = _configured_backend(files)
    panels = [panel for panel in list_panels() if panel.scope == "run"]
    if cls is None:
        # No backend chosen yet, or one this Studio cannot import: we know
        # nothing about its capabilities, so offer everything rather than hide
        # panels it may well support. The run's own gate has the last word.
        return tuple(panel.name for panel in panels)
    return backend_panel_names(backend_type, cls, panels)


def _persona_records(files: Mapping[str, str], item_key: str, context: PreviewContext) -> Any:
    from silisocs.runtime.construction.agent_builders.records import RecordLoader

    agents = yaml.safe_load(files.get("agents/default.yaml", "{}")) or {}
    classes = (agents.get("persona_pipeline") or {}).get("classes") or {}
    class_config = classes.get(item_key) if isinstance(classes, dict) else None
    if not isinstance(class_config, dict):
        raise ValueError(f"Unknown agent class {item_key!r}")
    data = class_config.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError(f"Agent class {item_key!r} data must be a mapping")
    records = RecordLoader(agents, project_root=context.repository_root).load_records(
        data, max_records=3
    )
    return {"source": data.get("source", "local_json"), "records": records}


register_choice_provider("backend.types", _backend_types)
register_choice_provider("backend.actions", _backend_actions, deferred=True)
register_choice_provider("panel.catalog", _panel_catalog, deferred=True)
register_choice_provider("llm.providers", _llm_providers)
register_choice_provider(
    "agent.classes", _catalog_provider("agent.classes", classes=True), deferred=True
)
register_choice_provider(
    "agent.builders", _catalog_provider("agent.builders", classes=True), deferred=True
)
register_choice_provider(
    "game_master.classes", _catalog_provider("game_master.classes", classes=True), deferred=True
)
register_choice_provider(
    "backend.classes",
    _catalog_provider("backend.classes", classes=True),
    deferred=True,
)


@cache
def _runtime_built_in_catalog() -> dict[str, tuple[str, ...]]:
    """Read implementation names from the runtime factories on first use."""
    from silisocs.environments.gm.components.factory import component_built_ins
    from silisocs.runtime.construction.engines import engine_strategy_built_ins
    from silisocs.simulation_engines.policies.factory import policy_built_ins

    return {
        **{f"component.{role}": values for role, values in component_built_ins().items()},
        **{
            f"policy.{role}": values
            for role, values in {
                **engine_strategy_built_ins(),
                **policy_built_ins(),
            }.items()
        },
    }


def _runtime_catalog_provider(catalog: str, *, classes: bool) -> ChoiceProvider:
    def provider(_: Mapping[str, str], context: ChoiceContext | None = None) -> Sequence[str]:
        built_ins = () if classes else _runtime_built_in_catalog().get(catalog, ())
        return tuple(
            dict.fromkeys((*built_ins, *_extension_choices(context, catalog, classes=classes)))
        )

    return provider


def _register_runtime_choice_providers() -> None:
    catalogs = {
        field.choices_from
        for field in form_schema("scenario").fields
        if field.choices_from
        and (
            field.choices_from.startswith("component.") or field.choices_from.startswith("policy.")
        )
    }
    for provider_name in sorted(catalogs):
        classes = provider_name.endswith(".classes")
        catalog = provider_name.removesuffix(".classes")
        register_choice_provider(
            provider_name,
            _runtime_catalog_provider(catalog, classes=classes),
            deferred=True,
        )


_register_runtime_choice_providers()
register_preview_provider("persona.records", _persona_records)


def _policy_estimate(
    builder: Callable[[Any], Any],
    slot: Any,
    method_name: str,
    *,
    path: str,
    findings: list[dict[str, str]],
    default: float = 1.0,
) -> float:
    """Build the real runtime policy for a slot and ask it for its estimate.

    Estimate semantics live on the policy classes themselves
    (``expected_active_share`` / ``expected_actions_per_turn``), so preflight
    can never drift from scheduling behavior. A slot that fails to build is an
    error for built-ins (the run would fail at startup the same way) but only
    a warning for a custom ``class_path`` (its import may need the run
    environment); a policy without the estimate hook contributes the
    conservative default — one action by every agent.
    """
    try:
        policy = builder(slot or {})
    except Exception as exc:
        findings.append(
            {
                "severity": "warning" if (slot or {}).get("class_path") else "error",
                "path": path,
                "message": f"Could not build this policy for the estimate: {exc}",
            }
        )
        return default
    method = getattr(policy, method_name, None)
    if not callable(method):
        return default
    try:
        return float(method())
    except Exception as exc:
        # A built-in whose estimate blows up (e.g. count: "abc" — dataclasses
        # don't validate) would crash the run at its first scheduling call the
        # same way, so it is an error; only a custom class_path degrades.
        findings.append(
            {
                "severity": "warning" if (slot or {}).get("class_path") else "error",
                "path": path,
                "message": f"The policy's {method_name}() estimate failed: {exc}",
            }
        )
        return default


def preflight_payload(files: dict[str, str]) -> dict[str, Any]:
    """Validate composer documents and estimate scale without executing a run."""
    findings: list[dict[str, str]] = []
    parsed: dict[str, dict[str, Any]] = {}
    for relative, text in files.items():
        try:
            value = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            findings.append({"severity": "error", "path": relative, "message": str(exc)})
            continue
        if not isinstance(value, dict):
            findings.append(
                {"severity": "error", "path": relative, "message": "Expected a YAML mapping"}
            )
            continue
        parsed[relative] = value

    # Scenarios may ship world/<name>.yaml (resource_market, virtual_space) instead
    # of the default variant, so resolve the actual group files rather than assume
    # the default names — otherwise num_agents/num_steps read 0 and backend checks
    # are skipped for those scenarios.
    names = list(files)
    world = parsed.get(_group_file("world", names) or "world/default.yaml", {})
    sim = parsed.get(_group_file("sim", names) or "sim.yaml", {})
    env = parsed.get(_group_file("env", names) or "env.yaml", {})

    def integer(value: Any, path: str, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            findings.append({"severity": "error", "path": path, "message": "Must be an integer"})
            return default

    def number(value: Any, path: str, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            findings.append({"severity": "error", "path": path, "message": "Must be numeric"})
            return default

    agents = integer(world.get("num_agents", 0), "world.num_agents")
    steps = integer(world.get("num_steps", 0), "world.num_steps")
    if agents <= 0:
        findings.append(
            {"severity": "error", "path": "world.num_agents", "message": "Must be positive"}
        )
    if steps <= 0:
        findings.append(
            {"severity": "error", "path": "world.num_steps", "message": "Must be positive"}
        )
    backend = ((env.get("gm") or {}).get("backend") or {}) if isinstance(env, dict) else {}
    if backend:
        from silisocs.environments.backends.factory import resolve_backend_class

        try:
            backend_class = resolve_backend_class(
                str(backend.get("type") or ""),
                class_path=str(backend.get("class_path") or "") or None,
            )
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            findings.append({"severity": "error", "path": "env.gm.backend", "message": str(exc)})
        else:
            known_actions = {
                name
                for action in backend_class.declared_action_catalog()
                for name in (action["name"], action["selectable_name"])
            }
            configured_actions = {
                str(name)
                for key in ("enabled_actions", "excluded_actions")
                for name in (backend.get(key) or [])
            }
            unknown_actions = sorted(configured_actions - known_actions)
            if unknown_actions:
                findings.append(
                    {
                        "severity": "error",
                        "path": "env.gm.backend.enabled_actions",
                        "message": f"Unknown backend actions: {', '.join(unknown_actions)}",
                    }
                )

    llm = sim.get("llm") or {}
    provider = str(llm.get("provider") or "")
    if provider and not llm.get("disabled") and not llm.get("api_key"):
        from silisocs.runtime.language_models.catalog import OPENAI_COMPATIBLE_PRESETS

        key_env = (
            "OPENAI_API_KEY"
            if provider == "openai"
            else OPENAI_COMPATIBLE_PRESETS.get(provider, ("", None))[1]
        )
        if key_env and not os.environ.get(key_env):
            findings.append(
                {
                    "severity": "warning",
                    "path": "sim.llm.provider",
                    "message": f"{key_env} is not set in the Studio process",
                }
            )

    engine = sim.get("engine") or {}
    actions_per_turn = _policy_estimate(
        build_turn_policy,
        engine.get("turn_policy"),
        "expected_actions_per_turn",
        path="sim.engine.turn_policy",
        findings=findings,
    )
    active_fraction = _policy_estimate(
        build_participation_policy,
        engine.get("participation"),
        "expected_active_share",
        path="sim.engine.participation",
        findings=findings,
    )
    active_fraction = min(1.0, max(0.0, active_fraction))
    calls = round(agents * steps * active_fraction * actions_per_turn)
    estimate_cfg = sim.get("preflight") or {}
    prompt_tokens = integer(
        estimate_cfg.get("prompt_tokens_per_call", 1200),
        "sim.preflight.prompt_tokens_per_call",
        1200,
    )
    completion_tokens = integer(
        estimate_cfg.get("completion_tokens_per_call", 180),
        "sim.preflight.completion_tokens_per_call",
        180,
    )
    pricing = llm.get("pricing") or {}
    input_price = number(pricing.get("input_per_1m", 0), "sim.llm.pricing.input_per_1m", 0.0)
    output_price = number(pricing.get("output_per_1m", 0), "sim.llm.pricing.output_per_1m", 0.0)
    estimated_cost = (
        calls * prompt_tokens / 1_000_000 * input_price
        + calls * completion_tokens / 1_000_000 * output_price
    )
    return {
        "ok": not any(item["severity"] == "error" for item in findings),
        "findings": findings,
        "estimate": {
            "agent_steps": round(agents * steps * active_fraction),
            "actions": calls,
            "llm_calls": calls,
            "prompt_tokens": calls * prompt_tokens,
            "completion_tokens": calls * completion_tokens,
            "cost_usd": round(estimated_cost, 4) if pricing else None,
        },
    }


_GROUP_FILES = {
    "world": "world/default.yaml",
    "agents": "agents/default.yaml",
    "sim": "sim.yaml",
    "env": "env.yaml",
    "eval": "eval.yaml",
}


def _group_file(group: str, available: Sequence[str]) -> str | None:
    """The document a composer field's group reads from and writes to.

    A scenario may name its group files after itself (``world/resource_market.yaml``,
    selected with ``world=resource_market``) instead of shipping the default
    variant. Writing to the fixed default name would add a SECOND option to the
    same Hydra group and silently change which one a run composes, so a lone
    variant is the target when the default is absent.
    """
    default = _GROUP_FILES.get(group)
    if default is None or "/" not in default or default in set(available):
        return default
    variants = sorted(
        name
        for name in available
        if name.startswith(f"{group}/") and name.endswith(".yaml") and name.count("/") == 1
    )
    return variants[0] if len(variants) == 1 else default


def field_values(files: dict[str, str], schema: FormSchema | None = None) -> dict[str, Any]:
    """Project known schema fields from YAML documents without altering unknown keys."""
    documents = {name: yaml.safe_load(text) or {} for name, text in files.items()}
    values: dict[str, Any] = {}
    names = list(files)
    for item in (schema or form_schema()).fields:
        group, *parts = item.key.split(".")
        value: Any = documents.get(_group_file(group, names) or "", {})
        for part in parts:
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        values[item.key] = value
    return values


# The generic component pipeline a non-social backend needs, written into a
# scenario's env.yaml whenever the composer points it at one. ``params: null``
# rather than ``{}`` is load-bearing: a scenario env.yaml is MERGED over the
# default (social) env group, and OmegaConf.merge cannot clear sibling keys — an
# empty mapping leaves the social params (graph, recsys, timeline) in place, and
# the generic component then rejects them as unsupported. Null replaces the node.
_GENERIC_COMPONENTS: dict[str, str] = {
    "initialize": "app_initialize",
    "observe": "app_observation",
    "update": "none",
    "action_prompt": "default",
}


def _generic_components_block(resolve_built_in: str) -> dict[str, Any]:
    slots = {**_GENERIC_COMPONENTS, "resolve": resolve_built_in}
    return {
        role: {"built_in": built_in, "class_path": None, "params": None}
        for role, built_in in slots.items()
    }


def _is_generated_component_slot(slot: Any) -> bool:
    """True only for a slot this composer wrote itself (the bare generic block).

    ``_generic_components_block`` emits exactly ``{built_in, class_path: None,
    params: None}``. Matching that precise shape distinguishes the composer's own
    output from a user-authored slot carrying params or a class_path, so
    re-deriving components on a backend-type change never clobbers hand config.
    """
    return (
        isinstance(slot, dict)
        and set(slot) == {"built_in", "class_path", "params"}
        and slot.get("class_path") is None
        and slot.get("params") is None
    )


def _apply_backend_component_defaults(documents: dict[str, Any], touched: set[str]) -> None:
    """Keep a draft's GM components compatible with the backend it selects.

    The composer's scenario runs against the default (social) env group, whose
    components call SocialBackendApp-only methods. Selecting a non-social backend
    without replacing them produces a scenario that composes cleanly and then
    fails at run time, so the components a backend needs follow the backend.

    Only ever run when the backend *type/class* changes (the caller gates this),
    and even then only touch slots this composer itself wrote — a user's authored
    observe/action_prompt/init config must survive a backend edit untouched.
    """
    from silisocs.environments.backends.base import SocialBackendApp

    names = list(documents)
    env_file = _group_file("env", names) or "env.yaml"
    env = documents.get(env_file)
    if not isinstance(env, dict):
        return
    files = {name: yaml.safe_dump(document) for name, document in documents.items()}
    _, cls = _configured_backend(files)
    if cls is None:  # Unimportable: say nothing rather than guess (preflight reports it).
        return
    # Coerce ``gm``/``components`` that a draft may carry as null or a non-mapping
    # (e.g. ``gm: {components: null}``) before mutating, so a hand-written scalar
    # never surfaces as an unhandled AttributeError.
    gm = env.get("gm")
    if not isinstance(gm, dict):
        gm = {}
        env["gm"] = gm
    components = gm.get("components")
    if not isinstance(components, dict):
        components = {}
        gm["components"] = components
    if issubclass(cls, SocialBackendApp):
        # The social env group already supplies these; reclaim only the generic
        # block this composer previously wrote, never a user-authored slot.
        for role in (*_GENERIC_COMPONENTS, "resolve"):
            if _is_generated_component_slot(components.get(role)):
                components.pop(role, None)
        if not components:
            gm.pop("components", None)
    else:
        sim_file = _group_file("sim", names) or "sim.yaml"
        sim = documents.get(sim_file)
        tool_calling = ((sim or {}).get("tool_calling") or {}).get("mode") if sim else None
        # action_mode is set to ``generic`` below; the canonical resolve for generic
        # output is ``generic_action`` (parsed_action needs an ACTION TYPE block and
        # a SocialBackendApp-only method, so it fails on generic output).
        resolve = (
            "tool_calling" if str(tool_calling or "") in {"single", "multi"} else "generic_action"
        )
        # Fill only slots that are absent or hold our own prior generic block;
        # never overwrite a slot the user customized with params or a class_path.
        for role, slot in _generic_components_block(resolve).items():
            if role not in components or _is_generated_component_slot(components.get(role)):
                components[role] = slot
        # The social group's prompt text is written for a timeline; a generic
        # backend describes itself through its own action catalog instead.
        if isinstance(sim, dict):
            sim["action_mode"] = "generic"
            touched.add(sim_file)
    touched.add(env_file)


def compose_files(files: dict[str, str], updates: dict[str, Any]) -> dict[str, str]:
    """Apply dotted schema updates to YAML documents while preserving every other key.

    Documents an update does not touch pass through byte-for-byte; touched
    documents are re-serialized but keep their leading comment block (which is
    where the ``# @package`` directives live).
    """
    documents = {name: yaml.safe_load(text) or {} for name, text in files.items()}
    touched: set[str] = set()
    names = list(files)
    for key, value in updates.items():
        group, *parts = str(key).split(".")
        relative = _group_file(group, names)
        if relative is None or not parts:
            raise ValueError(f"Unknown composer field path {key!r}")
        document = documents.setdefault(relative, {})
        touched.add(relative)
        cursor = document
        for part in parts[:-1]:
            child = cursor.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"Cannot write {key!r}; {part!r} is not a mapping")
            cursor = child
        cursor[parts[-1]] = value
    # Re-derive GM components only when the backend TYPE/class actually changes,
    # never on other backend.* edits (enabled_actions, params) — those must not
    # disturb authored component config.
    if any(str(key) in ("env.gm.backend.type", "env.gm.backend.class_path") for key in updates):
        _apply_backend_component_defaults(documents, touched)
    composed = {}
    for relative, document in documents.items():
        if relative in touched:
            text = leading_comment_block(files.get(relative, "")) + yaml.safe_dump(
                document, sort_keys=False, allow_unicode=True
            )
        else:
            text = files[relative]
        composed[relative] = ensure_package_directive(relative, text)
    return composed
