# silisocs/runtime/config.py
"""Configuration helpers and validators for composed scenario configs.

This module provides validation helpers used by the experiment runner to ensure
the composed Hydra scenario configuration conforms to expected shapes. Semantic
world values come from the ``world`` config group, but validation runs after all
config groups have been merged.

The validation functions raise :class:`ValueError` or :class:`FileNotFoundError`
when checks fail.
"""

import difflib
import importlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hydra.experimental.callback import Callback
from omegaconf import DictConfig, ListConfig, OmegaConf
from omegaconf.errors import InterpolationResolutionError

from silisocs.runtime.model_fields import MODEL_FIELDS

# Shared config-key vocabularies (single source of truth for both the config
# validator here and the GM-spec builder in construction/game_masters.py).
COMPONENT_SLOT_NAMES: tuple[str, ...] = (
    "initialize",
    "next_acting",
    "action_prompt",
    "observe",
    "resolve",
    "update",
)
BACKEND_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"type", "class_path", "params", "enabled_actions", "excluded_actions", "action_aliases"}
)
# Framework-defined keys under sim.engine.step.params (apply across built-in step
# modes; a custom class_path step strategy may use its own params, so this set is
# only enforced for built-in step strategies).
ENGINE_STEP_PARAM_KEYS: frozenset[str] = frozenset(
    {"flow_order", "agent_to_flow", "gm_turn_policies", "gm_concurrency_caps", "flow_turn_policies"}
)
# The uniform policy-slot shape ({built_in|class_path, params}) used by every
# sim.engine sub-slot.
_POLICY_SLOT_KEYS: frozenset[str] = frozenset({"built_in", "class_path", "params"})


@dataclass
class BaseScenarioSchema:
    """
    Base schema that all scenarios must conform to.
    Semantic world values are supplied by the scenario's ``world`` config.
    """

    # Scenario identification
    scenario_name: str

    # Setting information
    setting: dict[str, Any] = field(
        default_factory=lambda: {
            "name": "",
            "background": [],
        }
    )

    # Setting information
    event: dict[str, Any] = field(
        default_factory=lambda: {
            "name": "",
            "context": "",
        }
    )

    # Data sources (optional, world-specific)
    data: dict[str, Any] = field(default_factory=dict)

    # Agent configuration
    roles: dict[str, int] = field(default_factory=dict)  # role -> count
    role_configs: dict[str, Any] = field(default_factory=dict)  # role -> config

    # Shared memories and observations
    shared_memories: list[str] = field(default_factory=list)
    initial_observations: list[str] = field(default_factory=list)

    # Probes configuration
    probes: dict[str, Any] = field(default_factory=dict)

    # Output configuration
    jobname_format: str = ""


def validate_scenario_structure(cfg: DictConfig) -> None:
    """
    Validate that scenario config has all required fields.

    Args:
        cfg: scenario configuration to validate

    Raises
    ------
        ValueError: If required fields are missing
    """
    has_class_pipeline = bool(
        OmegaConf.select(cfg, "agents.persona_pipeline.classes")
        or OmegaConf.select(cfg, "persona_pipeline.classes")
    )
    required_fields = ["scenario_name", "setting"]
    if has_class_pipeline:
        # Class-pipeline scenarios can define shared memories at the persona
        # defaults level and do not need initial_observations.
        has_top_level_shared = (
            OmegaConf.select(cfg, "agents.shared_memories") is not None
            or OmegaConf.select(cfg, "shared_memories") is not None
        )
        has_pipeline_shared = (
            OmegaConf.select(cfg, "agents.persona_pipeline.defaults.shared_memories") is not None
            or OmegaConf.select(cfg, "persona_pipeline.defaults.shared_memories") is not None
        )
        if not (has_top_level_shared or has_pipeline_shared):
            missing_fields = ["shared_memories or persona_pipeline.defaults.shared_memories"]
            raise ValueError(
                f"scenario configuration missing required fields: {', '.join(missing_fields)}\n"
                "Please ensure your scenario config includes all required fields."
            )
    else:
        required_fields.extend(["roles", "shared_memories", "initial_observations"])

    missing_fields = []
    for field_name in required_fields:
        if not OmegaConf.select(cfg, field_name):
            missing_fields.append(field_name)

    if missing_fields:
        raise ValueError(
            f"scenario configuration missing required fields: {', '.join(missing_fields)}\n"
            "Please ensure your scenario config includes all required fields."
        )

    # Validate nested required fields
    if OmegaConf.select(cfg, "setting"):
        setting_required = ["name", "background"]
        for field_name in setting_required:
            if not OmegaConf.select(cfg, f"setting.{field_name}"):
                missing_fields.append(f"setting.{field_name}")

        # Validate nested required fields
    if OmegaConf.select(cfg, "event"):
        event_required = ["name", "context"]
        for field_name in event_required:
            if not OmegaConf.select(cfg, f"event.{field_name}"):
                missing_fields.append(f"event.{field_name}")

    if missing_fields:
        raise ValueError(
            f"scenario configuration missing required nested fields: {', '.join(missing_fields)}"
        )

    print("✓ Scenario structure validation passed")


def validate_cross_references(cfg: DictConfig) -> None:
    """
    Validate that cross-references in config are consistent.

    Args:
        cfg: Full experiment configuration

    Raises
    ------
        ValueError: If cross-references are invalid
    """
    errors = []

    world_cfg = cfg

    # Validate that probe candidate references exist
    if hasattr(world_cfg, "probes"):
        probes_cfg = world_cfg.probes

        # Get candidate names from refactored structure first.
        candidate_names: list[str] = []
        if hasattr(world_cfg, "candidates"):
            for _partisan_type, info in world_cfg.candidates.items():
                if hasattr(info, "name"):
                    candidate_names.append(str(info.name))
                elif isinstance(info, dict) and "name" in info:
                    candidate_names.append(str(info["name"]))

        # Validate probe references
        probes_to_validate = getattr(probes_cfg, "probes", None)

        if probes_to_validate is not None:
            for probe_num, probe_data in probes_to_validate.items():
                if hasattr(probe_data, "interaction_premise_template"):
                    premise = probe_data.interaction_premise_template
                elif hasattr(probe_data, "probe_data") and hasattr(
                    probe_data.probe_data, "interaction_premise_template"
                ):
                    premise = probe_data.probe_data.interaction_premise_template
                else:
                    continue

                # Check candidate references
                for field in ["candidate", "candidate1", "candidate2"]:
                    if hasattr(premise, field):
                        candidate = getattr(premise, field)
                        if candidate and candidate not in candidate_names:
                            errors.append(
                                f"Probe {probe_num} references unknown candidate: {candidate}"
                            )

    roles_cfg = OmegaConf.select(world_cfg, "roles") or {}
    roles = set(roles_cfg.keys()) if isinstance(roles_cfg, dict) else set()
    activity_rates = OmegaConf.select(
        world_cfg,
        "env.gm.components.next_acting.params.activity_transition_rates",
    )
    if activity_rates and roles:
        for role in activity_rates.keys():
            if role not in roles:
                errors.append(f"Next-acting activity rates reference unknown role: {role}")

    graph_cfg = OmegaConf.select(world_cfg, "env.gm.components.initialize.params.graph")
    fully_connected_targets = (
        graph_cfg.get("fully_connected_targets", []) if isinstance(graph_cfg, dict) else []
    )
    for role in fully_connected_targets:
        if roles and role not in roles:
            errors.append(
                f"GM initialize graph fully_connected_targets references unknown role: {role}"
            )

    # Validate fixed-action set references in class pipeline.
    class_pipeline = OmegaConf.select(world_cfg, "persona_pipeline.classes")
    if class_pipeline:
        inline_sets = OmegaConf.select(world_cfg, "fixed_action_sets.inline") or {}
        file_sets = OmegaConf.select(world_cfg, "fixed_action_sets.file")
        available_set_names = set(inline_sets.keys()) if isinstance(inline_sets, dict) else set()

        # File-based set names are validated at build-time once file is loaded.
        # Here we only assert that refs are not empty and that inline refs exist.
        for class_name, class_cfg in class_pipeline.items():
            class_cfg = class_cfg or {}
            fixed_cfg = class_cfg.get("fixed_action") if isinstance(class_cfg, dict) else None
            if not isinstance(fixed_cfg, dict):
                continue
            if not bool(fixed_cfg.get("enabled", False)):
                continue
            set_ref = str(fixed_cfg.get("action_set_ref", "")).strip()
            if not set_ref:
                errors.append(
                    f"Class `{class_name}` has fixed_action.enabled=true but no action_set_ref"
                )
                continue
            if set_ref not in available_set_names and not file_sets:
                errors.append(
                    f"Class `{class_name}` references unknown fixed_action set `{set_ref}`"
                )

    if errors:
        raise ValueError(
            "Cross-reference validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    print("✓ Cross-reference validation passed")


def validate_data_files(cfg: DictConfig, scenario_path: Path) -> None:
    """
    Validate that referenced data files exist.

    Args:
        cfg: scenario configuration
        scenario_path: Path to scenario directory

    Raises
    ------
        FileNotFoundError: If required files don't exist
    """
    missing_files = []
    class_pipeline = OmegaConf.select(cfg, "persona_pipeline.classes")

    def _resolve_local_path(raw_path: str) -> Path | None:
        """_resolve_local_path.

        :param str raw_path:
        :type raw_path: str

        :returns: Path | None
        :rtype: Path | None
        """
        if not raw_path:
            return None
        candidate_paths = [
            Path(raw_path),
            scenario_path / raw_path,
            scenario_path / "input" / raw_path,
            scenario_path / "input" / "personas" / raw_path,
            scenario_path / "input" / "news_data" / raw_path,
        ]
        for candidate in candidate_paths:
            if candidate.exists():
                return candidate
        return None

    # Validate persona file
    if not class_pipeline and hasattr(cfg, "data") and hasattr(cfg.data, "persona_file"):
        persona_file = scenario_path / "input" / "personas" / cfg.data.persona_file
        if not persona_file.exists():
            missing_files.append(str(persona_file))

    # Validate news file if news agent is used
    if hasattr(cfg, "data") and hasattr(cfg.data, "use_news_agent"):
        if cfg.data.use_news_agent and cfg.data.use_news_agent != "none":
            if hasattr(cfg.data, "news_file"):
                news_file = scenario_path / "input" / "news_data" / f"{cfg.data.news_file}.json"
                if not news_file.exists():
                    missing_files.append(str(news_file))

    if class_pipeline:
        for class_name, class_cfg in class_pipeline.items():
            class_cfg = class_cfg or {}
            data_cfg = class_cfg.get("data", {})
            data_source = data_cfg.get("source")
            data_path = data_cfg.get("path") or data_cfg.get("dataset")
            if data_source == "local_json":
                resolved_data_path = _resolve_local_path(str(data_path))
                if resolved_data_path is None:
                    missing_files.append(f"class `{class_name}` data path not found: {data_path}")

            shared_memories = class_cfg.get("shared_memories")
            if isinstance(shared_memories, dict):
                shared_path = shared_memories.get("path")
                if shared_path and _resolve_local_path(str(shared_path)) is None:
                    missing_files.append(
                        f"class `{class_name}` shared memories path not found: {shared_path}"
                    )

    fixed_action_file = OmegaConf.select(cfg, "fixed_action_sets.file")
    if fixed_action_file:
        resolved = _resolve_local_path(str(fixed_action_file))
        if resolved is None:
            missing_files.append(f"fixed_action_sets.file path not found: {fixed_action_file}")

    if missing_files:
        raise FileNotFoundError(
            "Data file validation failed. Missing files:\n"
            + "\n".join(f"  - {f}" for f in missing_files)
        )

    print("✓ Data file validation passed")


def validate_runtime_structure(cfg: DictConfig) -> None:
    """Validate framework-owned config sections while leaving params open."""
    _assert_allowed_keys(cfg, "agents.builder", {"class_path", "params"})
    _assert_allowed_keys(cfg, "agents.persona_pipeline", {"defaults", "classes"})
    _validate_persona_classes(cfg)

    _assert_allowed_keys(
        cfg,
        "env",
        {"gm", "gm_orchestration"},
    )
    _assert_allowed_keys(
        cfg,
        "env.gm",
        {
            "backend",
            "components",
            "name",
            "class_path",
            "action_mode",
            "tool_calling_mode",
            "tool_calling",
        },
    )
    _assert_allowed_keys(cfg, "env.gm.backend", set(BACKEND_ALLOWED_KEYS))
    _assert_allowed_keys(cfg, "env.gm_orchestration", {"gms", "flow_bindings"})
    _assert_allowed_keys(cfg, "env.gm_orchestration.flow_bindings", {"flow_to_gms"})
    _assert_allowed_keys(cfg, "env.gm.components", set(COMPONENT_SLOT_NAMES))
    for slot in COMPONENT_SLOT_NAMES:
        _assert_component_slot(cfg, f"env.gm.components.{slot}")

    _assert_allowed_keys(
        cfg,
        "sim",
        {
            "llm",
            "max_concurrent_actions",
            "action_mode",
            "tool_calling",
            "prompt_additions",
            "initialization",
            "memory",
            "checkpoint",
            "engine",
            "telemetry",
            "roleplaying_instructions",
        },
    )
    _assert_allowed_keys(cfg, "sim.memory", set(_POLICY_SLOT_KEYS))
    _assert_allowed_keys(cfg, "sim.telemetry", {"record_active_agent_names"})
    # ``pricing`` is telemetry-only (cost reporting): read by the usage summary,
    # ignored by model construction/dedup (build_global_llm_config reads only
    # MODEL_FIELDS), so it must be allowed here without entering MODEL_FIELDS.
    _assert_allowed_keys(cfg, "sim.llm", set(MODEL_FIELDS) | {"pricing"})
    _assert_allowed_keys(cfg, "sim.llm.pricing", {"input_per_1m", "output_per_1m"})
    provider = OmegaConf.select(cfg, "sim.llm.provider")
    if provider is not None:
        from silisocs.runtime.language_models.registry import get_llm_provider

        provider_text = str(provider)
        is_built_in = provider_text in {"openai", "openai_compatible", "scripted", "disabled"}
        is_registered = get_llm_provider(provider_text) is not None
        is_class_path = "." in provider_text
        if not (is_built_in or is_registered or is_class_path):
            raise ValueError(
                f"Unsupported sim.llm.provider: {provider!r}. Use a built-in "
                "(openai, openai_compatible, scripted, disabled), a provider "
                "registered via @register_llm_provider, or a class path."
            )
    _assert_allowed_keys(
        cfg,
        "sim.engine",
        {
            "class_path",
            "params",
            "loop",
            "step",
            "turn_policy",
            "participation",
            "executor",
            "control",
        },
    )
    executor = OmegaConf.select(cfg, "sim.engine.executor")
    if executor is not None and str(executor).strip().lower() not in {"threads", "asyncio"}:
        raise ValueError(
            f"Unsupported sim.engine.executor: {executor!r}. Use 'threads' (default) or 'asyncio'."
        )
    _validate_control_config(cfg)
    _validate_engine_slots(cfg)
    _assert_allowed_keys(cfg, "eval", {"probes"})
    _validate_interventions_config(cfg)
    print("✓ Runtime section validation passed")


_PERSONA_BUILDER_CLASS_PATH = (
    "silisocs.runtime.construction.agent_builders.persona_pipeline.PersonaPipelineAgentBuilder"
)


def _validate_persona_classes(cfg: DictConfig) -> None:
    """Reject unknown sub-keys under ``agents.persona_pipeline.classes.<class>``.

    The persona-pipeline builder reads a fixed set of sub-keys, so anything else is a
    typo that would otherwise be silently ignored (``flow_tags`` instead of
    ``flow_tag`` leaves every agent in the default flow). A custom
    ``agents.builder.class_path`` defines its own class vocabulary, so the check is
    skipped there.
    """
    builder = str(OmegaConf.select(cfg, "agents.builder.class_path") or "").strip()
    if builder and builder != _PERSONA_BUILDER_CLASS_PATH:
        return
    classes = OmegaConf.select(cfg, "agents.persona_pipeline.classes")
    if not isinstance(classes, (Mapping, DictConfig)):
        return
    # Imported lazily: construction imports this module, so a top-level import
    # would be circular.
    from silisocs.runtime.construction.agent_builders.persona_pipeline import PERSONA_CLASS_KEYS

    for class_name, class_cfg in classes.items():
        if not isinstance(class_cfg, (Mapping, DictConfig)):
            continue
        for key in (str(k) for k in class_cfg):
            if key in PERSONA_CLASS_KEYS:
                continue
            close = difflib.get_close_matches(key, sorted(PERSONA_CLASS_KEYS), n=1, cutoff=0.6)
            suggestion = f" Did you mean `{close[0]}`?" if close else ""
            raise ValueError(
                f"Unsupported config key `{key}` under "
                f"agents.persona_pipeline.classes.{class_name}.{suggestion} "
                f"Known keys: {sorted(PERSONA_CLASS_KEYS)}. Per-agent constructor "
                "arguments belong under `params`."
            )


def _validate_control_config(cfg: DictConfig) -> None:
    """Validate the optional ``sim.engine.control`` interactive-run slot."""
    control = OmegaConf.select(cfg, "sim.engine.control")
    if control is None:
        return
    _assert_allowed_keys(
        cfg,
        "sim.engine.control",
        {"built_in", "start_paused", "control_file", "poll_interval"},
    )
    built_in = str(OmegaConf.select(cfg, "sim.engine.control.built_in") or "none").strip()
    if built_in not in {"none", "stdin", "control_file"}:
        raise ValueError(
            f"Unsupported sim.engine.control.built_in: {built_in!r}. "
            "Use 'none' (default), 'stdin', or 'control_file'."
        )
    start_paused = OmegaConf.select(cfg, "sim.engine.control.start_paused")
    if start_paused is not None and not isinstance(start_paused, bool):
        raise ValueError("sim.engine.control.start_paused must be a boolean")
    poll_interval = OmegaConf.select(cfg, "sim.engine.control.poll_interval")
    if poll_interval is not None:
        if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)):
            raise ValueError("sim.engine.control.poll_interval must be a positive number")
        if float(poll_interval) <= 0:
            raise ValueError("sim.engine.control.poll_interval must be greater than zero")


def _validate_interventions_config(cfg: DictConfig) -> None:
    """Validate the optional top-level ``interventions`` schedule (if present)."""
    from silisocs.simulation_engines.interventions import validate_interventions

    validate_interventions(
        cfg, gm_names=_declared_gm_names(cfg), flow_names=_declared_flow_names(cfg)
    )


def _validate_engine_slots(cfg: DictConfig) -> None:
    """Validate the shape of each ``sim.engine`` sub-slot and its GM-scoped maps.

    Without this, a typo in the newest, most actively-configured knobs (e.g.
    ``sim.engine.step.buit_in`` or a bogus ``step.params`` key) would silently fall
    back to defaults instead of failing loudly.
    """
    for slot in ("step", "loop", "turn_policy", "participation"):
        _assert_allowed_keys(cfg, f"sim.engine.{slot}", set(_POLICY_SLOT_KEYS))

    # step.params keys are framework-defined for built-in step strategies; a custom
    # class_path step strategy may carry its own params, so only enforce for built-ins.
    step_class_path = OmegaConf.select(cfg, "sim.engine.step.class_path")
    if not step_class_path:
        step_params = OmegaConf.select(cfg, "sim.engine.step.params")
        if isinstance(step_params, (Mapping, DictConfig)) and "chain_execution" in step_params:
            # Preserve the dedicated migration hint (also raised at engine-build time)
            # instead of a generic "unsupported key" error.
            raise ValueError(
                "sim.engine.step.params.chain_execution has been removed. Select the "
                "traversal via sim.engine.step.built_in instead: 'multi_gm' (concurrent, "
                "the default), 'multi_gm_serial' (legacy flow-by-flow), or 'multi_gm_staged' "
                "(global per-stage barrier)."
            )
        _assert_allowed_keys(cfg, "sim.engine.step.params", set(ENGINE_STEP_PARAM_KEYS))

    _validate_gm_scoped_maps(cfg)


def _declared_gm_names(cfg: DictConfig) -> set[str]:
    """Collect GM names declared in config (single ``env.gm`` and/or orchestration)."""
    names: set[str] = set()
    single = OmegaConf.select(cfg, "env.gm.name")
    if single:
        names.add(str(single))
    gms = OmegaConf.select(cfg, "env.gm_orchestration.gms")
    if isinstance(gms, (list, ListConfig)):
        for gm in gms:
            if not isinstance(gm, (Mapping, DictConfig)):
                continue
            # Construction resolves each GM's name from ``gm_name`` first, then
            # ``name`` (build_game_masters); mirror that precedence so per-GM
            # validation (interventions, gm_turn_policies) sees the real names.
            name = gm.get("gm_name") or gm.get("name")
            if name:
                names.add(str(name))
    return names


def _declared_flow_names(cfg: DictConfig) -> set[str]:
    """Collect flow names statically declared in config, for preflight validation.

    Sources mirror how the engine materializes ``agent_flow_tags``: per-class
    ``flow_tag``, ``flow_order``, ``agent_to_flow`` values, and the keys of
    ``flow_bindings.flow_to_gms``. An empty set means flows are not statically
    known (e.g. a custom step strategy) and callers defer to fire-time checks;
    when any flow IS declared, the implicit ``default`` flow (agents without a
    ``flow_tag``) is included.
    """
    names: set[str] = set()
    classes = OmegaConf.select(cfg, "agents.persona_pipeline.classes")
    if isinstance(classes, (Mapping, DictConfig)):
        for spec in classes.values():
            if isinstance(spec, (Mapping, DictConfig)) and spec.get("flow_tag"):
                names.add(str(spec["flow_tag"]))
    flow_order = OmegaConf.select(cfg, "sim.engine.step.params.flow_order")
    if isinstance(flow_order, (list, ListConfig)):
        names.update(str(flow) for flow in flow_order if flow)
    agent_to_flow = OmegaConf.select(cfg, "sim.engine.step.params.agent_to_flow")
    if isinstance(agent_to_flow, (Mapping, DictConfig)):
        names.update(str(flow) for flow in agent_to_flow.values() if flow)
    flow_to_gms = OmegaConf.select(cfg, "env.gm_orchestration.flow_bindings.flow_to_gms")
    if isinstance(flow_to_gms, (Mapping, DictConfig)):
        names.update(str(flow) for flow in flow_to_gms if flow)
    if names:
        names.add("default")
    return names


def _validate_gm_scoped_maps(cfg: DictConfig) -> None:
    """Reject unknown GM names in the per-GM maps under ``sim.engine.step.params``.

    Mirrors the loud-failure standard set by the retired ``chain_execution`` knob:
    a typo'd GM key would otherwise silently no-op.
    """
    declared = _declared_gm_names(cfg)
    if not declared:
        return
    for key in ("gm_turn_policies", "gm_concurrency_caps"):
        mapping = OmegaConf.select(cfg, f"sim.engine.step.params.{key}")
        if not isinstance(mapping, (Mapping, DictConfig)):
            continue
        unknown = sorted(str(name) for name in mapping if str(name) not in declared)
        if unknown:
            raise ValueError(
                f"sim.engine.step.params.{key} references unknown game master(s) "
                f"{unknown}; declared GMs are {sorted(declared)}. Check the GM name(s)."
            )


def _assert_allowed_keys(cfg: DictConfig, path: str, allowed: set[str]) -> None:
    value = OmegaConf.select(cfg, path)
    if value is None:
        return
    if not isinstance(value, DictConfig):
        raise ValueError(f"{path} must be a mapping.")
    extras = sorted(str(key) for key in value.keys() if str(key) not in allowed)
    if extras:
        raise ValueError(f"Unsupported config key(s) under {path}: {extras}")


def validate_component_slot_shape(value: Any, *, path: str) -> None:
    """Validate one GM component slot's shape (allowed keys + nested instances).

    Accepts either a raw ``DictConfig`` node (config-validation phase) or an
    already-extracted mapping (GM-spec construction), so both phases share the
    same structural rules.
    """
    if not isinstance(value, (Mapping, DictConfig)):
        raise ValueError(f"{path} must be a mapping.")
    allowed = {"built_in", "class_path", "params", "instances", "flow_map"}
    extras = sorted(str(key) for key in value if str(key) not in allowed)
    if extras:
        raise ValueError(f"Unsupported config key(s) under {path}: {extras}")
    instances = value.get("instances")
    if instances is None:
        return
    if not isinstance(instances, (Mapping, DictConfig)):
        raise ValueError(f"{path}.instances must be a mapping.")
    for instance_name, instance_cfg in instances.items():
        instance_path = f"{path}.instances.{instance_name!s}"
        if not isinstance(instance_cfg, (Mapping, DictConfig)):
            raise ValueError(f"{instance_path} must be a mapping.")
        instance_extras = sorted(
            str(key) for key in instance_cfg if str(key) not in {"built_in", "class_path", "params"}
        )
        if instance_extras:
            raise ValueError(f"Unsupported config key(s) under {instance_path}: {instance_extras}")


def _assert_component_slot(cfg: DictConfig, path: str) -> None:
    value = OmegaConf.select(cfg, path)
    if value is None:
        return
    validate_component_slot_shape(value, path=path)


def _collect_class_paths(node: Any, location: str, found: list[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            child_location = f"{location}.{key}" if location else str(key)
            if str(key) == "class_path" and isinstance(value, str) and value.strip():
                found.append((location or "<root>", value.strip()))
            else:
                _collect_class_paths(value, child_location, found)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _collect_class_paths(item, f"{location}[{index}]", found)


def validate_class_paths(cfg: DictConfig) -> None:
    """Verify every configured ``class_path`` is importable before any LLM call.

    Without this, a typo'd class path only surfaces at runtime-assembly time,
    after models are constructed and (for checkpoint resumes) state is loaded.
    """
    container = OmegaConf.to_container(cfg, resolve=False)
    found: list[tuple[str, str]] = []
    _collect_class_paths(container, "", found)

    errors: list[str] = []
    for location, class_path in found:
        if "${" in class_path:
            continue  # interpolation resolved later; cannot check statically
        module_path, _, attr = class_path.rpartition(".")
        if not module_path:
            errors.append(
                f"{location}: '{class_path}' is not a fully qualified class path "
                "(expected 'package.module.ClassName')"
            )
            continue
        try:
            getattr(importlib.import_module(module_path), attr)
        except (ImportError, AttributeError) as exc:
            errors.append(f"{location}: cannot import '{class_path}': {exc}")
    if errors:
        raise ValueError(
            "Configured class_path entries failed to import:\n  - " + "\n  - ".join(errors)
        )
    print(f"✓ class_path validation passed ({len(found)} checked)")


def validate_scenario_config(cfg: DictConfig, scenario_path: Path | None = None) -> None:
    """
    Run all validation checks on scenario configuration.

    Args:
        cfg: Configuration to validate
        scenario_path: Path to scenario directory (for data file validation)

    Raises
    ------
        Various exceptions if validation fails
    """
    print("\n" + "=" * 60)
    print("VALIDATING SCENARIO CONFIGURATION")
    print("=" * 60)

    # 1. Validate structure
    validate_scenario_structure(cfg)
    validate_runtime_structure(cfg)
    validate_class_paths(cfg)

    # 2. Validate cross-references
    validate_cross_references(cfg)

    # 3. Validate data files (if path provided)
    if scenario_path:
        validate_data_files(cfg, scenario_path)

    print("=" * 60)
    print("✅ ALL VALIDATION CHECKS PASSED")
    print("=" * 60 + "\n")


# Universal run params a scenario's world group must provide, in the order a
# fresh world/default.yaml should declare them.
_UNIVERSAL_WORLD_PARAMS = (
    'jobname_format: "N${num_agents}_T${num_steps}_${experiment_name}_${run_name}"',
    "scenario_name: my_scenario",
    "run_name: baseline",
    "output_rootname: outputs",
    "num_agents: 10",
    "num_steps: 5",
    "seed: 1",
)


class RunDirInterpolationCheck(Callback):
    """Explain an unresolvable ``hydra.run.dir`` before Hydra dies on it.

    Hydra resolves the run dir itself, before the task function runs, so the
    failure surfaces as a bare ``InterpolationKeyError: 'jobname_format'`` with no
    hint that the cause is a scenario ``world/default.yaml`` shadowing the base
    world group (Hydra searchpath replacement) and dropping the universal run
    params with it. This callback runs at ``on_run_start`` (single run) and
    ``on_multirun_start`` (``-m``/sweep) — early enough to replace that message
    with an actionable one in both launch modes.
    """

    # Keys Hydra resolves before the task function. A single run resolves the run
    # dir + output subdir; a multirun additionally resolves the sweep dir and job
    # name, any of which references the dropped universal run params.
    _RUN_KEYS = ("hydra.run.dir", "hydra.output_subdir")
    _MULTIRUN_KEYS = ("hydra.sweep.dir", "hydra.output_subdir", "hydra.job.name")

    def on_run_start(self, config: DictConfig, **kwargs: Any) -> None:
        """Resolve the run dir eagerly, translating a missing key into guidance."""
        self._check_keys(config, self._RUN_KEYS)

    def on_multirun_start(self, config: DictConfig, **kwargs: Any) -> None:
        """Run the same check for ``-m``/multirun, which resolves sweep keys too."""
        self._check_keys(config, self._MULTIRUN_KEYS)

    def _check_keys(self, config: DictConfig, keys: tuple[str, ...]) -> None:
        for key in keys:
            try:
                OmegaConf.select(config, key, throw_on_resolution_failure=True)
            except InterpolationResolutionError as exc:
                raise ValueError(self._message(key, exc)) from exc

    @staticmethod
    def _message(key: str, exc: InterpolationResolutionError) -> str:
        missing = str(getattr(exc, "key", "") or "").strip() or "a referenced key"
        declarations = "\n".join(f"  {line}" for line in _UNIVERSAL_WORLD_PARAMS)
        return (
            f"Config error: {key} references '{missing}', which is not defined.\n\n"
            "This usually means your scenario's world/default.yaml REPLACES the base world "
            "config group (Hydra shadows a group file of the same name rather than merging "
            "it), so every universal run param the base provided must be re-declared there.\n\n"
            "Add the missing keys to <scenario>/conf/world/default.yaml, which must start "
            "with the '# @package _global_' directive:\n"
            f"{declarations}\n\n"
            "Flat scenario sim.yaml/env.yaml files are MERGED and need no such re-declaration; "
            "only config-group files (world/, agents/, env/) replace their group. "
            "See docs/configuration.md."
        )
