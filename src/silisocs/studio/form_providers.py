"""The silisocs composer content: the scenario schema and its capability providers.

Every provider here reads a runtime catalog (backends, LLM providers, GM
components, engine policies, analysis panels), so each one imports its layer
inside its own body: registering a provider must stay cheap even though running
one is not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import cache
from typing import TYPE_CHECKING, Any

import yaml

from silisocs.studio.compose import field_values
from silisocs.studio.form_schema import (
    ChoiceContext,
    ChoiceProvider,
    Field,
    FormSchema,
    PreviewContext,
    choice_items,
    choice_provider_is_deferred,
    form_schema,
    load_widget,
    register_choice_provider,
    register_form_schema,
    register_preview_provider,
    run_choice_provider,
)

if TYPE_CHECKING:  # Type-only: the backend layer stays a lazy import at runtime.
    from silisocs.environments.backends.base import BackendApp


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
            deferred = defer_expensive and choice_provider_is_deferred(field.choices_from)
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
    from silisocs.environments.backends.factory import registered_backend_types  # noqa: PLC0415

    return (*registered_backend_types(), "custom")


def _llm_providers(_: Mapping[str, str]) -> Sequence[str]:
    from silisocs.runtime.language_models.catalog import (  # noqa: PLC0415
        BUILT_IN_PROVIDERS,
        OPENAI_COMPATIBLE_PRESETS,
    )
    from silisocs.runtime.language_models.registry import available_llm_providers  # noqa: PLC0415

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


def configured_backend(files: Mapping[str, str]) -> tuple[str, type[BackendApp] | None]:
    """The backend a draft scenario selects: its type, and its class if importable."""
    from silisocs.environments.backends.factory import resolve_backend_class  # noqa: PLC0415

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
    _, cls = configured_backend(files)
    if cls is None:
        return ()
    return tuple(item["selectable_name"] for item in cls.declared_action_catalog())


def _panel_catalog(files: Mapping[str, str]) -> Sequence[str]:
    """Run panels the drafted backend can actually feed.

    The same capability declarations that gate a finished run's views gate the
    composer, so you cannot build a view out of panels your scenario's backend
    will never populate.
    """
    from silisocs.analysis.panel import list_panels  # noqa: PLC0415
    from silisocs.studio.capabilities import backend_panel_names  # noqa: PLC0415

    backend_type, cls = configured_backend(files)
    panels = [panel for panel in list_panels() if panel.scope == "run"]
    if cls is None:
        # No backend chosen yet, or one this Studio cannot import: we know
        # nothing about its capabilities, so offer everything rather than hide
        # panels it may well support. The run's own gate has the last word.
        return tuple(panel.name for panel in panels)
    return backend_panel_names(backend_type, cls, panels)


def _persona_records(files: Mapping[str, str], item_key: str, context: PreviewContext) -> Any:
    from silisocs.runtime.construction.agent_builders.records import RecordLoader  # noqa: PLC0415

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
    from silisocs.environments.gm.components.factory import component_built_ins  # noqa: PLC0415
    from silisocs.runtime.construction.engines import engine_strategy_built_ins  # noqa: PLC0415
    from silisocs.simulation_engines.policies.factory import policy_built_ins  # noqa: PLC0415

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


def runtime_catalog_provider_for(name: str) -> ChoiceProvider | None:
    """A provider for an unregistered ``component.*``/``policy.*`` catalog name.

    This is the fallback :func:`~silisocs.studio.form_schema.run_choice_provider`
    consults on a miss: a schema shipped by a plugin may reference a runtime
    catalog that ``_register_runtime_choice_providers`` (which walks the scenario
    schema only) never saw. ``None`` means the name is genuinely unknown.
    """
    classes = name.endswith(".classes")
    catalog = name.removesuffix(".classes")
    if catalog.startswith(("component.", "policy.")) and catalog in _runtime_built_in_catalog():
        return _runtime_catalog_provider(catalog, classes=classes)
    return None


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
