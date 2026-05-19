"""Game master runtime-spec construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from omegaconf import DictConfig, OmegaConf

from silisocs.environments.backends.social.network import get_simrole_parameters
from silisocs.runtime.configuration.legacy import reject_removed_runtime_keys
from silisocs.runtime.configuration.projection import RuntimeProjection
from silisocs.runtime.construction.engines import default_gm_filename, default_gm_module_path
from silisocs.runtime.construction.specs import GameMasterConfig, SimRole

DEFAULT_FLOW_TAG = "default"


def _env_cfg(cfg: Any) -> Any:
    return getattr(cfg, "env", getattr(cfg, "environment", object()))


def _collect_declared_flow_tags(cfg: DictConfig) -> set[str]:
    declared: set[str] = set()
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
    gm_prompt_cfg: Mapping[str, Any] | None = None,
) -> str:
    from silisocs.runtime.prompts.action_prompts import build_action_prompt_with_app_instance

    action_mode = str(getattr(cfg.sim, "action_mode", "custom") or "custom").strip().lower()
    if action_mode == "generic":
        return ""

    return build_action_prompt_with_app_instance(
        cfg=cfg,
        action_mode=action_mode,
        tool_calling_mode=tool_calling_mode,
        gm_prompt_cfg=gm_prompt_cfg,
    )


def _gm_runtime_config(cfg: DictConfig) -> dict[str, Any]:
    sim_view = {
        "action_mode": OmegaConf.select(cfg, "sim.action_mode", default="custom"),
        "tool_calling": OmegaConf.select(cfg, "sim.tool_calling", default={}) or {},
        "prompt_additions": OmegaConf.select(cfg, "sim.prompt_additions", default={}) or {},
        "engine": {
            "turn_policy": OmegaConf.select(cfg, "sim.engine.turn_policy", default={}) or {},
        },
    }
    return {
        "output_rootname": str(OmegaConf.select(cfg, "output_rootname", default="") or ""),
        "env": cast(dict[str, Any], OmegaConf.to_container(_env_cfg(cfg), resolve=True)),
        "sim": cast(
            dict[str, Any], OmegaConf.to_container(OmegaConf.create(sim_view), resolve=True)
        ),
    }


def _normalise_gm_initializer_cfg(raw: Any, *, index: int) -> dict[str, Any]:
    if raw is None:
        raise ValueError(
            f"env.gm_orchestration.gms[{index}] is missing required components.initialize."
        )
    if isinstance(raw, DictConfig):
        raw = OmegaConf.to_container(raw, resolve=True)
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"env.gm_orchestration.gms[{index}].components.initialize must be a mapping."
        )
    cfg = dict(raw)
    if not str(cfg.get("built_in") or "").strip() and not str(cfg.get("class_path") or "").strip():
        raise ValueError(
            f"env.gm_orchestration.gms[{index}].components.initialize must set "
            "built_in or class_path."
        )
    cfg.setdefault("class_path", None)
    cfg.setdefault("params", {})
    return cfg


def _default_gm_initializer_cfg(cfg: DictConfig) -> dict[str, Any]:
    if OmegaConf.select(cfg, "env.gm.initializer") is not None:
        raise ValueError(
            "`env.gm.initializer` has been removed. Use "
            "`env.gm.components.initialize` for GM-owned backend setup."
        )
    configured = OmegaConf.select(cfg, "env.gm.components.initialize")
    if configured is not None:
        if isinstance(configured, DictConfig):
            configured = OmegaConf.to_container(configured, resolve=True)
        if not isinstance(configured, Mapping):
            raise ValueError("env.gm.components.initialize must be a mapping.")
        return _normalise_gm_initializer_cfg(configured, index=0)
    platform_type = str(getattr(_env_cfg(cfg), "platform_type", "") or "").strip()
    social_platforms = {"twitter_like", "reddit_like", "mastodon", "oasis_twitter", "oasis_reddit"}
    built_in = "social_media" if platform_type in social_platforms else "app_initialize"
    return {"built_in": built_in, "class_path": None, "params": {}}


def _resolve_gm_specs(cfg: DictConfig) -> list[dict[str, Any]]:
    default_gm = _env_cfg(cfg).gamemaster
    default_initializer = _default_gm_initializer_cfg(cfg)
    default_mode = "shared"
    default_spec = {
        "gm_name": str(default_gm.name),
        "filename": default_gm_filename(cfg, default_mode),
        "sim_role_name": str(default_gm.sim_role.name),
        "sim_role_module_path": default_gm_module_path(cfg, default_mode),
        "sequence": 0,
        "mode": default_mode,
        "backend_scope": "shared_default",
        "initializer": default_initializer,
    }

    gm_orchestration_cfg = getattr(_env_cfg(cfg), "gm_orchestration", None)
    if gm_orchestration_cfg is None:
        gm_orchestration_cfg = getattr(getattr(cfg, "sim", object()), "gm_orchestration", object())
    gm_specs_raw = getattr(gm_orchestration_cfg, "gms", None)
    if (
        not isinstance(gm_specs_raw, Sequence)
        or isinstance(gm_specs_raw, (str, bytes))
        or not gm_specs_raw
    ):
        return [default_spec]

    specs: list[dict[str, Any]] = []
    for idx, gm_raw in enumerate(gm_specs_raw):
        if not isinstance(gm_raw, Mapping):
            raise ValueError(f"env.gm_orchestration.gms[{idx}] must be a mapping.")
        sim_role_cfg = gm_raw.get("sim_role", {})
        if not isinstance(sim_role_cfg, Mapping):
            sim_role_cfg = {}
        spec: dict[str, Any] = {
            "gm_name": str(
                gm_raw.get("gm_name", gm_raw.get("name", default_spec["gm_name"])) or ""
            ).strip(),
            "mode": str(gm_raw.get("mode", "shared") or "shared").strip(),
        }
        spec["filename"] = str(
            gm_raw.get("filename", default_gm_filename(cfg, str(spec["mode"]))) or ""
        ).strip()
        spec.update(
            {
                "sim_role_name": str(
                    sim_role_cfg.get("name", default_spec["sim_role_name"]) or ""
                ).strip(),
                "sim_role_module_path": str(
                    sim_role_cfg.get(
                        "module_path",
                        default_gm_module_path(cfg, str(spec["mode"])),
                    )
                    or ""
                ).strip(),
                "sequence": int(gm_raw.get("sequence", idx)),
                "backend_scope": str(
                    gm_raw.get("backend_scope", "shared_default") or "shared_default"
                ).strip(),
                "initializer": _normalise_gm_initializer_cfg(
                    gm_raw.get("initializer"),
                    index=idx,
                ),
            }
        )
        prompt_cfg = gm_raw.get("prompt", {})
        if prompt_cfg is None:
            prompt_cfg = {}
        if not isinstance(prompt_cfg, Mapping):
            raise ValueError(
                f"env.gm_orchestration.gms[{idx}].prompt must be a mapping when provided."
            )
        spec["prompt"] = dict(prompt_cfg)
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
    gm_orchestration_cfg = getattr(_env_cfg(cfg), "gm_orchestration", None)
    if gm_orchestration_cfg is None:
        gm_orchestration_cfg = getattr(getattr(cfg, "sim", object()), "gm_orchestration", object())
    bindings = getattr(gm_orchestration_cfg, "flow_bindings", None)
    if isinstance(bindings, Mapping):
        for legacy_key in ("flow_to_gm", "gm_to_flows"):
            legacy_value = bindings.get(legacy_key, {})
            if isinstance(legacy_value, Mapping) and legacy_value:
                raise ValueError(
                    f"env.gm_orchestration.flow_bindings.{legacy_key} is removed. "
                    "Use flow_to_gms only."
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
    reject_removed_runtime_keys(cfg)

    gm_specs = _resolve_gm_specs(cfg)
    declared_flows = _collect_declared_flow_tags(cfg)
    flow_chains = _resolve_flow_chains(cfg, gm_specs, declared_flows)

    activity_transition_rates = dict(
        OmegaConf.select(cfg, "env.social_network.activity_transition_rates")
        or OmegaConf.select(cfg, "sim.social_network.activity_transition_rates")
        or {}
    )
    fully_connected_targets = list(
        OmegaConf.select(cfg, "env.social_network.fully_connected_targets")
        or OmegaConf.select(cfg, "sim.social_network.fully_connected_targets")
        or []
    )
    simrole_params = get_simrole_parameters(
        activity_transition_rates=activity_transition_rates,
        roles=list(activity_transition_rates.keys()),
        fully_connected_targets=fully_connected_targets,
        base_probability=(
            OmegaConf.select(cfg, "env.social_network.base_followership_probability")
            or OmegaConf.select(cfg, "sim.social_network.base_followership_probability")
            or 0.4
        ),
    )

    projection = RuntimeProjection.from_cfg(cfg)
    social_media_gms: list[GameMasterConfig] = []
    for spec in gm_specs:
        gm_name = str(spec["gm_name"])
        owned_flows = [flow for flow, chain in flow_chains.items() if gm_name in chain]
        sim_role = SimRole(
            name=str(spec["sim_role_name"]),
            module_path=str(spec["sim_role_module_path"]),
        )
        gm_user_data = {
            "sim_role_parameters": dict(simrole_params),
            "sim_roles": {},
            "gm_orchestration": {
                "gm_name": gm_name,
                "sequence": int(spec["sequence"]),
                "mode": str(spec["mode"]),
                "backend_scope": str(spec["backend_scope"]),
                "owned_flows": owned_flows,
                "flow_chains": flow_chains,
                "prompt": dict(spec.get("prompt") or {}),
            },
        }
        action_prompt = _build_action_prompt(
            cfg,
            tool_calling_mode=projection.tool_calling_mode,
            gm_prompt_cfg=cast(Mapping[str, Any] | None, spec.get("prompt")),
        )
        gm_params = {
            "name": gm_name,
            "calls_to_action": {
                "environment_action": action_prompt,
                "social_media_action": action_prompt,
            },
            "sim_role": sim_role,
            "app_module_path": getattr(_env_cfg(cfg), "app_module_path", ""),
            "environment_data": gm_user_data,
            "sm_user_data": gm_user_data,
            "app_description": getattr(_env_cfg(cfg), "usage_instructions", ""),
            "runtime_config": _gm_runtime_config(cfg),
            "initializer": dict(spec["initializer"]),
        }
        gm_class_name = (
            "FlowRoutedGameMaster"
            if str(spec["filename"]) == "shared_flow_game_master"
            else "GameMaster"
        )
        social_media_gms.append(
            GameMasterConfig(
                class_path=f"{spec['sim_role_module_path']}.{gm_class_name}",
                params=gm_params,
            )
        )

    return social_media_gms
