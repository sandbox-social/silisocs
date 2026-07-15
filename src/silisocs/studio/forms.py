"""Declarative composer schemas and filesystem-backed scenario documents."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

import yaml

SCENARIO_FILES = (
    "world/default.yaml",
    "agents/default.yaml",
    "sim.yaml",
    "env.yaml",
    "eval.yaml",
)


def _scenario_relative_files(conf_dir: Path) -> list[str]:
    files = [relative for relative in SCENARIO_FILES if (conf_dir / relative).is_file()]
    files.extend(
        path.relative_to(conf_dir).as_posix()
        for path in sorted((conf_dir / "views").glob("*.yaml"))
        if path.is_file()
    )
    return files


def _supported_scenario_file(relative: str) -> bool:
    path = Path(relative)
    return relative in SCENARIO_FILES or (
        len(path.parts) == 2 and path.parts[0] == "views" and path.suffix == ".yaml"
    )


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


_SCHEMAS: dict[str, FormSchema] = {}
ChoiceProvider = Callable[[Mapping[str, str]], Sequence[str]]
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


def run_choice_provider(name: str, files: Mapping[str, str]) -> list[str]:
    """Resolve one registered choice capability from the current documents."""
    try:
        provider = _CHOICE_PROVIDERS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown choice provider {name!r}") from exc
    return [str(choice) for choice in provider(files)]


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
            item["choices"] = [] if deferred else run_choice_provider(field.choices_from, files)
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
                "mapping",
                "Agent classes",
                "Agents",
                help="Each key is a class id; each value is its declarative class configuration",
                preview_from="persona.records",
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
                "text",
                "Custom backend class",
                "Platform",
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
            Field("sim.engine.step.built_in", "select", "Step strategy", "Advanced", advanced=True),
            Field(
                "sim.engine.turn_policy.built_in",
                "select",
                "Turn policy",
                "Advanced",
                advanced=True,
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


def _backend_actions(files: Mapping[str, str]) -> Sequence[str]:
    from silisocs.environments.backends.factory import resolve_backend_class

    env = yaml.safe_load(files.get("env.yaml", "{}")) or {}
    backend = ((env.get("gm") or {}).get("backend") or {}) if isinstance(env, dict) else {}
    try:
        cls = resolve_backend_class(
            str(backend.get("type") or ""),
            class_path=str(backend.get("class_path") or "") or None,
        )
    except (ImportError, AttributeError, TypeError, ValueError):
        return ()
    return tuple(item["selectable_name"] for item in cls.declared_action_catalog())


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
register_choice_provider("llm.providers", _llm_providers)
register_preview_provider("persona.records", _persona_records)


class ScenarioRepository:
    """Read and write scenario YAML without discarding unknown fields."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def validate_name(name: str) -> str:
        clean = str(name).strip()
        if not clean or any(part in clean for part in ("/", "\\", "..")):
            raise ValueError("Scenario name must be a safe directory name")
        return clean

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        return [
            {
                "name": path.name,
                "path": str(path),
                "files": _scenario_relative_files(path / "conf"),
            }
            for path in sorted(self.root.iterdir())
            if path.is_dir() and (path / "conf").is_dir()
        ]

    def count(self) -> int:
        """Count scenario config roots without loading their documents."""
        if not self.root.is_dir():
            return 0
        return sum(1 for path in self.root.iterdir() if (path / "conf").is_dir())

    def load(self, name: str, *, parse: bool = True) -> dict[str, Any]:
        name = self.validate_name(name)
        scenario_dir = self.root / name
        if not scenario_dir.is_dir():
            raise KeyError(name)
        files: dict[str, Any] = {}
        for relative in _scenario_relative_files(scenario_dir / "conf"):
            path = scenario_dir / "conf" / relative
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            files[relative] = {
                "text": text,
                "data": yaml.safe_load(text) if parse else None,
            }
        return {"name": name, "path": str(scenario_dir), "files": files}

    def save(self, name: str, files: dict[str, str]) -> dict[str, Any]:
        name = self.validate_name(name)
        unknown = sorted(relative for relative in files if not _supported_scenario_file(relative))
        if unknown:
            raise ValueError(f"Unsupported scenario files: {unknown}")
        parsed: dict[str, Any] = {}
        for relative, text in files.items():
            data = yaml.safe_load(text) if text.strip() else {}
            if not isinstance(data, dict):
                raise ValueError(f"{relative} must contain a YAML mapping")
            parsed[relative] = data
        for relative, data in parsed.items():
            path = self.root / name / "conf" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
            )
        return self.load(name)


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

    world = parsed.get("world/default.yaml", {})
    sim = parsed.get("sim.yaml", {})
    env = parsed.get("env.yaml", {})

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

    turn = ((sim.get("engine") or {}).get("turn_policy") or {}).get("built_in") or "single_action"
    turn_params = ((sim.get("engine") or {}).get("turn_policy") or {}).get("params") or {}
    if turn == "fixed_count":
        actions_per_turn = integer(
            turn_params.get("count", 1), "sim.engine.turn_policy.params.count", 1
        )
    elif turn == "open_ended":
        actions_per_turn = integer(
            turn_params.get("max_actions", 1),
            "sim.engine.turn_policy.params.max_actions",
            1,
        )
        if "max_actions" not in turn_params:
            findings.append(
                {
                    "severity": "warning",
                    "path": "sim.engine.turn_policy.params.max_actions",
                    "message": "Open-ended estimate uses one action without a configured cap",
                }
            )
    else:
        actions_per_turn = 1
    participation = ((sim.get("engine") or {}).get("participation") or {}).get("params") or {}
    active_fraction = number(
        participation.get("active_probability", 1.0),
        "sim.engine.participation.params.active_probability",
        1.0,
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


def field_values(files: dict[str, str], schema: FormSchema | None = None) -> dict[str, Any]:
    """Project known schema fields from YAML documents without altering unknown keys."""
    documents = {name: yaml.safe_load(text) or {} for name, text in files.items()}
    values: dict[str, Any] = {}
    for item in (schema or form_schema()).fields:
        group, *parts = item.key.split(".")
        value: Any = documents.get(_GROUP_FILES.get(group, ""), {})
        for part in parts:
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        values[item.key] = value
    return values


def compose_files(files: dict[str, str], updates: dict[str, Any]) -> dict[str, str]:
    """Apply dotted schema updates to YAML documents while preserving every other key."""
    documents = {name: yaml.safe_load(text) or {} for name, text in files.items()}
    for key, value in updates.items():
        group, *parts = str(key).split(".")
        relative = _GROUP_FILES.get(group)
        if relative is None or not parts:
            raise ValueError(f"Unknown composer field path {key!r}")
        document = documents.setdefault(relative, {})
        cursor = document
        for part in parts[:-1]:
            child = cursor.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"Cannot write {key!r}; {part!r} is not a mapping")
            cursor = child
        cursor[parts[-1]] = value
    return {
        relative: yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        for relative, document in documents.items()
    }
