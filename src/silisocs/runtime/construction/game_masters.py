"""Game master runtime-spec construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.configuration.projection import RuntimeProjection
from silisocs.runtime.configuration.validation import validate_runtime_structure
from silisocs.runtime.construction.specs import GameMasterConfig

DEFAULT_FLOW_TAG = "default"


def _collect_declared_flow_tags(cfg: DictConfig) -> set[str]:
    declared: set[str] = set()
    agent_to_flow = OmegaConf.select(cfg, "sim.engine.step.params.agent_to_flow", default={})
    if isinstance(agent_to_flow, Mapping):
        for flow_tag in agent_to_flow.values():
            normalized = str(flow_tag or "").strip()
            if normalized:
                declared.add(normalized)

    classes_cfg = (
        getattr(getattr(cfg.agents, "persona_pipeline", object()), "classes", None)
        if hasattr(cfg, "agents")
        else None
    )
    if not isinstance(classes_cfg, Mapping):
        return declared

    for class_cfg in classes_cfg.values():
        if not isinstance(class_cfg, Mapping):
            continue
        flow_tag = str(class_cfg.get("flow_tag", "") or "").strip()
        if flow_tag:
            declared.add(flow_tag)
    return declared


def _build_action_prompt(
    cfg: DictConfig,
    tool_calling_mode: str,
    action_prompt_params: Mapping[str, Any] | None = None,
) -> str:
    from silisocs.runtime.prompts.action_prompts import build_action_prompt_with_app_instance

    action_mode = str(getattr(cfg.sim, "action_mode", "custom") or "custom").strip().lower()
    if action_mode == "generic":
        return ""

    return build_action_prompt_with_app_instance(
        cfg=cfg,
        action_mode=action_mode,
        tool_calling_mode=tool_calling_mode,
        gm_prompt_cfg=action_prompt_params,
    )


def _normalise_gm_initializer_cfg(raw: Any, *, path: str) -> dict[str, Any]:
    if raw is None:
        raise ValueError(f"{path} is missing required initialize component.")
    if isinstance(raw, DictConfig):
        raw = OmegaConf.to_container(raw, resolve=True)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}.initialize must be a mapping.")
    cfg = dict(raw)
    if not str(cfg.get("built_in") or "").strip() and not str(cfg.get("class_path") or "").strip():
        raise ValueError(f"{path}.initialize must set built_in or class_path.")
    cfg.setdefault("class_path", None)
    cfg.setdefault("params", {})
    return cfg


def _default_gm_initializer_cfg(cfg: DictConfig) -> dict[str, Any]:
    configured = OmegaConf.select(cfg, "env.gm.components.initialize")
    if configured is None:
        raise ValueError("env.gm.components.initialize is required.")
    if isinstance(configured, DictConfig):
        configured = OmegaConf.to_container(configured, resolve=True)
    if not isinstance(configured, Mapping):
        raise ValueError("env.gm.components.initialize must be a mapping.")
    return _normalise_gm_initializer_cfg(configured, path="env.gm.components")


def _plain_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, DictConfig):
        value = OmegaConf.to_container(value, resolve=True)
    if not isinstance(value, Mapping):
        raise ValueError(f"Expected mapping config, got {type(value).__name__}.")
    return dict(value)


def _validate_component_slot_shape(value: Any, *, path: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a mapping.")
    allowed = {"built_in", "class_path", "params", "instances", "flow_map"}
    extras = sorted(str(key) for key in value if str(key) not in allowed)
    if extras:
        raise ValueError(f"Unsupported config key(s) under {path}: {extras}")
    instances = value.get("instances")
    if instances is None:
        return
    if not isinstance(instances, Mapping):
        raise ValueError(f"{path}.instances must be a mapping.")
    for instance_name, instance_cfg in instances.items():
        instance_path = f"{path}.instances.{instance_name!s}"
        if not isinstance(instance_cfg, Mapping):
            raise ValueError(f"{instance_path} must be a mapping.")
        instance_extras = sorted(
            str(key) for key in instance_cfg if str(key) not in {"built_in", "class_path", "params"}
        )
        if instance_extras:
            raise ValueError(f"Unsupported config key(s) under {instance_path}: {instance_extras}")


def _validate_components_cfg(components: Mapping[str, Any], *, path: str) -> None:
    allowed_slots = {"initialize", "next_acting", "action_prompt", "observe", "resolve", "update"}
    extras = sorted(str(key) for key in components if str(key) not in allowed_slots)
    if extras:
        raise ValueError(f"Unsupported config key(s) under {path}: {extras}")
    for slot, slot_cfg in components.items():
        _validate_component_slot_shape(slot_cfg, path=f"{path}.{slot}")


def _gm_components_cfg(cfg: DictConfig) -> dict[str, Any]:
    components = _plain_mapping(OmegaConf.select(cfg, "env.gm.components", default={}) or {})
    _validate_components_cfg(components, path="env.gm.components")
    return components


def _backend_config(
    cfg: DictConfig,
    raw_backend: Mapping[str, Any],
    *,
    path: str,
) -> dict[str, Any]:
    unsupported = sorted(
        set(raw_backend) - {"type", "class_path", "params", "enabled_actions", "excluded_actions"}
    )
    if unsupported:
        raise ValueError(f"Unsupported config key(s) under {path}: {unsupported}")
    backend_type = str(raw_backend.get("type", "") or "").strip()
    if not backend_type:
        raise ValueError(f"{path}.type is required.")
    backend_params = _plain_mapping(raw_backend.get("params") or {})
    return {
        "backend_type": backend_type,
        "output_rootname": str(OmegaConf.select(cfg, "output_rootname", default="") or ""),
        "perform_operations": bool(backend_params.pop("perform_operations", False)),
        "app_description": str(backend_params.pop("app_description", "") or ""),
        "class_path": raw_backend.get("class_path"),
        "params": backend_params,
        "enabled_actions": raw_backend.get("enabled_actions"),
        "excluded_actions": raw_backend.get("excluded_actions"),
        "turn_policy_built_in": str(
            OmegaConf.select(cfg, "sim.engine.turn_policy.built_in", default="") or ""
        ),
    }


def _resolve_gm_specs(cfg: DictConfig) -> list[dict[str, Any]]:
    default_gm = cfg.env.gm
    default_class_path = str(
        OmegaConf.select(
            cfg,
            "env.gm.class_path",
            default="silisocs.environments.gm.game_master.ComponentGameMaster",
        )
        or "silisocs.environments.gm.game_master.ComponentGameMaster"
    )
    default_spec = {
        "gm_name": str(getattr(default_gm, "name", "environment_gm") or "environment_gm"),
        "class_path": default_class_path,
        "sequence": 0,
        "mode": "shared",
        "backend": _plain_mapping(OmegaConf.select(cfg, "env.gm.backend", default={}) or {}),
        "backend_path": "env.gm.backend",
        "components": _gm_components_cfg(cfg),
    }

    gm_orchestration_cfg = getattr(cfg.env, "gm_orchestration", None)
    gm_specs_raw = getattr(gm_orchestration_cfg, "gms", None)
    if (
        not isinstance(gm_specs_raw, Sequence)
        or isinstance(gm_specs_raw, (str, bytes))
        or not gm_specs_raw
    ):
        default_spec["initializer"] = _default_gm_initializer_cfg(cfg)
        return [default_spec]

    specs: list[dict[str, Any]] = []
    for idx, gm_raw in enumerate(gm_specs_raw):
        if not isinstance(gm_raw, Mapping):
            raise ValueError(f"env.gm_orchestration.gms[{idx}] must be a mapping.")
        unsupported = sorted(
            set(gm_raw)
            - {"gm_name", "name", "class_path", "sequence", "mode", "backend", "components"}
        )
        if unsupported:
            raise ValueError(
                f"Unsupported config key(s) under env.gm_orchestration.gms[{idx}]: {unsupported}"
            )
        raw_backend = gm_raw.get("backend")
        if raw_backend is None:
            raise ValueError(f"env.gm_orchestration.gms[{idx}].backend is required.")
        raw_components = gm_raw.get("components")
        if raw_components is None:
            raise ValueError(f"env.gm_orchestration.gms[{idx}].components is required.")
        components = _plain_mapping(raw_components)
        _validate_components_cfg(
            components,
            path=f"env.gm_orchestration.gms[{idx}].components",
        )
        _backend_config(
            cfg,
            _plain_mapping(raw_backend),
            path=f"env.gm_orchestration.gms[{idx}].backend",
        )
        spec: dict[str, Any] = {
            "gm_name": str(
                gm_raw.get("gm_name", gm_raw.get("name", default_spec["gm_name"])) or ""
            ).strip(),
            "mode": str(gm_raw.get("mode", "shared") or "shared").strip(),
        }
        spec["class_path"] = str(gm_raw.get("class_path", default_class_path) or "").strip()
        spec.update(
            {
                "sequence": int(gm_raw.get("sequence", idx)),
                "backend": _plain_mapping(raw_backend),
                "backend_path": f"env.gm_orchestration.gms[{idx}].backend",
                "components": components,
                "initializer": _normalise_gm_initializer_cfg(
                    components.get("initialize"),
                    path=f"env.gm_orchestration.gms[{idx}].components",
                ),
            }
        )
        if not spec["gm_name"]:
            raise ValueError(f"env.gm_orchestration.gms[{idx}] is missing gm_name/name.")
        specs.append(spec)

    names = [str(spec["gm_name"]) for spec in specs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate GM names in env.gm_orchestration.gms: {duplicates}")
    return specs


def _resolve_flow_chains(
    cfg: DictConfig,
    gm_specs: list[dict[str, Any]],
    declared_flows: set[str],
) -> dict[str, list[str]]:
    if not gm_specs:
        return {}

    gm_names = {str(spec["gm_name"]) for spec in gm_specs}
    gm_sequences = {str(spec["gm_name"]): int(spec["sequence"]) for spec in gm_specs}
    default_gm = str(min(gm_specs, key=lambda item: int(item["sequence"]))["gm_name"])

    chains: dict[str, list[str]] = {}
    gm_orchestration_cfg = getattr(cfg.env, "gm_orchestration", None)
    bindings = getattr(gm_orchestration_cfg, "flow_bindings", None)
    if isinstance(bindings, Mapping):
        unsupported = sorted(str(key) for key in bindings if str(key) != "flow_to_gms")
        if unsupported:
            raise ValueError(
                f"Unsupported config key(s) under env.gm_orchestration.flow_bindings: {unsupported}"
            )
        flow_to_gms = bindings.get("flow_to_gms", {})
        if isinstance(flow_to_gms, Mapping):
            for flow, gm_chain in flow_to_gms.items():
                flow_name = str(flow).strip()
                if not flow_name:
                    continue
                if isinstance(gm_chain, str):
                    gm_chain_list = [gm_chain]
                elif isinstance(gm_chain, Sequence) and not isinstance(gm_chain, (str, bytes)):
                    gm_chain_list = list(gm_chain)
                else:
                    raise ValueError(
                        f"flow_to_gms['{flow_name}'] must be a string or list of strings."
                    )
                resolved = [str(gm).strip() for gm in gm_chain_list if str(gm).strip()]
                if not resolved:
                    raise ValueError(f"flow_to_gms['{flow_name}'] cannot be empty.")
                unknown = [gm for gm in resolved if gm not in gm_names]
                if unknown:
                    raise ValueError(f"Unknown GMs in flow_to_gms['{flow_name}']: {unknown}")
                chains[flow_name] = resolved
        elif flow_to_gms:
            raise ValueError("env.gm_orchestration.flow_bindings.flow_to_gms must be a mapping.")

    for flow in sorted(declared_flows):
        chains.setdefault(flow, [default_gm])
    chains.setdefault(DEFAULT_FLOW_TAG, [default_gm])

    for flow_name, gm_chain in chains.items():
        if len(set(gm_chain)) != len(gm_chain):
            raise ValueError(f"Flow '{flow_name}' has duplicate GMs in chain: {gm_chain}")
        if len(gm_chain) < 2:
            continue
        for left, right in zip(gm_chain, gm_chain[1:], strict=False):
            if gm_sequences[left] >= gm_sequences[right]:
                raise ValueError(
                    "Flow chain must be strictly serial by sequence for multi-GM flows: "
                    f"flow='{flow_name}' chain={gm_chain}. "
                    "Ensure each subsequent GM has a higher sequence number."
                )

    return chains


def build_game_masters(cfg: DictConfig) -> list[GameMasterConfig]:
    validate_runtime_structure(cfg)

    gm_specs = _resolve_gm_specs(cfg)
    declared_flows = _collect_declared_flow_tags(cfg)
    flow_chains = _resolve_flow_chains(cfg, gm_specs, declared_flows)

    projection = RuntimeProjection.from_cfg(cfg)
    game_masters: list[GameMasterConfig] = []
    for spec in gm_specs:
        gm_name = str(spec["gm_name"])
        owned_flows = [flow for flow, chain in flow_chains.items() if gm_name in chain]
        gm_components_cfg = dict(spec["components"])
        action_prompt_params = dict(
            dict(gm_components_cfg.get("action_prompt", {}) or {}).get("params", {}) or {}
        )
        action_prompt = _build_action_prompt(
            cfg,
            tool_calling_mode=projection.tool_calling_mode,
            action_prompt_params=action_prompt_params,
        )
        gm_components_cfg["initialize"] = dict(spec["initializer"])
        gm_params = {
            "name": gm_name,
            "backend_config": _backend_config(
                cfg,
                spec["backend"],
                path=str(spec["backend_path"]),
            ),
            "components": gm_components_cfg,
            "action_prompt_template": action_prompt,
            "action_mode": projection.action_mode,
            "tool_calling_mode": projection.tool_calling_mode,
            "sim_roles": {},
            "agent_flow_tags": {},
            "owned_flows": owned_flows,
            "flow_chains": flow_chains,
            "prompt_config": dict(action_prompt_params),
            "sequence": int(spec["sequence"]),
            "mode": str(spec["mode"]),
        }
        game_masters.append(
            GameMasterConfig(
                class_path=str(spec["class_path"]),
                params=gm_params,
            )
        )

    return game_masters
