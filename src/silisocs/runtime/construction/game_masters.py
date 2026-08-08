"""Game master runtime-spec construction."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from omegaconf import DictConfig, OmegaConf

from silisocs.environments.backends.factory import resolve_backend_db_path
from silisocs.runtime.configuration.projection import (
    RuntimeProjection,
    normalize_action_mode,
    normalize_tool_calling_mode,
    validate_resolve_tool_calling,
)
from silisocs.runtime.configuration.validation import (
    BACKEND_ALLOWED_KEYS,
    COMPONENT_SLOT_NAMES,
    validate_component_slot_shape,
    validate_runtime_structure,
)
from silisocs.runtime.construction.specs import GameMasterConfig
from silisocs.simulation_engines.policies.routers import BranchSpec

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
    action_mode: str,
    tool_calling_mode: str,
    action_prompt_params: Mapping[str, Any] | None = None,
) -> str:
    from silisocs.runtime.prompts.action_prompts import build_action_prompt_with_app_instance

    action_mode = str(action_mode or "custom").strip().lower()
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


def _validate_components_cfg(components: Mapping[str, Any], *, path: str) -> None:
    extras = sorted(str(key) for key in components if str(key) not in COMPONENT_SLOT_NAMES)
    if extras:
        raise ValueError(f"Unsupported config key(s) under {path}: {extras}")
    for slot, slot_cfg in components.items():
        validate_component_slot_shape(slot_cfg, path=f"{path}.{slot}")


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
    unsupported = sorted(set(raw_backend) - BACKEND_ALLOWED_KEYS)
    if unsupported:
        raise ValueError(f"Unsupported config key(s) under {path}: {unsupported}")
    backend_type = str(raw_backend.get("type", "") or "").strip()
    if not backend_type:
        raise ValueError(f"{path}.type is required.")
    backend_params = _plain_mapping(raw_backend.get("params") or {})
    return {
        "backend_type": backend_type,
        "output_dir": str(OmegaConf.select(cfg, "output_dir", default="") or ""),
        "perform_operations": bool(backend_params.pop("perform_operations", False)),
        "app_description": str(backend_params.pop("app_description", "") or ""),
        "class_path": raw_backend.get("class_path"),
        "params": backend_params,
        "enabled_actions": raw_backend.get("enabled_actions"),
        "excluded_actions": raw_backend.get("excluded_actions"),
        "action_aliases": _plain_mapping(raw_backend.get("action_aliases") or {}) or None,
        "turn_policy_built_in": str(
            OmegaConf.select(cfg, "sim.engine.turn_policy.built_in", default="") or ""
        ),
    }


def _isolate_backend_paths(entries: list[tuple[str, dict[str, Any]]]) -> None:
    """Give each game master a distinct backend output path; reject collisions.

    ``entries`` is a list of ``(gm_name, backend_config)``. With more than one GM
    each backend's ``output_dir`` is nested under a per-GM subdirectory so
    same-type GMs do not share one ``<backend_type>.db`` / ``action_events.jsonl``
    and clobber one another on checkpoint restore. Single-GM runs keep the flat
    layout. Mutates each ``backend_config`` in place. Raises ``ValueError`` if two
    GMs still resolve to the same backend database path.
    """
    isolate = len(entries) > 1
    seen: dict[str, str] = {}
    for gm_name, backend_config in entries:
        if isolate:
            backend_config["output_dir"] = os.path.join(
                str(backend_config.get("output_dir") or ""), gm_name
            )
        # Resolve the db path through the same helper ``create_backend_app`` uses
        # so the guard checks the exact path that will be opened, including any
        # configured ``params.db_path`` override.
        configured_params = backend_config.get("params") or {}
        db_key = resolve_backend_db_path(
            str(backend_config.get("output_dir") or ""),
            str(backend_config["backend_type"]),
            db_path=configured_params.get("db_path"),
        )
        if db_key in seen:
            raise ValueError(
                f"Game masters {seen[db_key]!r} and {gm_name!r} resolve to the same backend "
                f"database path {db_key!r}; checkpoint restore would clobber one with the other. "
                "Give them distinct gm_name values or backend output paths."
            )
        seen[db_key] = gm_name


def _gm_tool_calling_mode(explicit: Any, alias: Any, *, path: str) -> str:
    """Resolve a per-GM tool-calling mode from the scalar ``tool_calling`` override.

    The single supported per-GM spelling is a scalar ``tool_calling: <none|single|multi>``
    (a sibling to the scalar ``action_mode``). Returns '' (unset) when ``tool_calling``
    is absent — the global ``sim.tool_calling.mode`` then governs. The retired
    ``tool_calling_mode`` key and the ``tool_calling: {mode: ...}`` block form each raise
    a migration error pointing at the scalar spelling (``explicit`` is the value of the
    retired ``tool_calling_mode`` key, ``alias`` the value of ``tool_calling``).
    """
    if explicit is not None:
        raise ValueError(
            f"{path}.tool_calling_mode is retired; use the scalar "
            f"{path}.tool_calling: <none|single|multi> instead."
        )
    if alias is None:
        return ""
    if isinstance(alias, Mapping):
        raise ValueError(
            f"{path}.tool_calling must be a scalar mode string (none|single|multi); "
            f"the '{{mode: ...}}' block form is retired — write {path}.tool_calling: <mode>."
        )
    return str(alias).strip()


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
        "action_mode": str(OmegaConf.select(cfg, "env.gm.action_mode", default="") or "").strip(),
        "tool_calling_mode": _gm_tool_calling_mode(
            OmegaConf.select(cfg, "env.gm.tool_calling_mode"),
            OmegaConf.select(cfg, "env.gm.tool_calling"),
            path="env.gm",
        ),
        "index": 0,
        "resolve_path": "env.gm.components.resolve.built_in",
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
            - {
                "gm_name",
                "name",
                "class_path",
                "sequence",
                "mode",
                "backend",
                "components",
                "restore",
                "action_mode",
                "tool_calling_mode",
                "tool_calling",
            }
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
                "action_mode": str(gm_raw.get("action_mode", "") or "").strip(),
                "tool_calling_mode": _gm_tool_calling_mode(
                    gm_raw.get("tool_calling_mode"),
                    gm_raw.get("tool_calling"),
                    path=f"env.gm_orchestration.gms[{idx}]",
                ),
                "index": idx,
                "resolve_path": (f"env.gm_orchestration.gms[{idx}].components.resolve.built_in"),
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
) -> dict[str, list[str | None | BranchSpec]]:
    if not gm_specs:
        return {}

    gm_names = {str(spec["gm_name"]) for spec in gm_specs}
    gm_sequences = {str(spec["gm_name"]): int(spec["sequence"]) for spec in gm_specs}
    default_gm = str(min(gm_specs, key=lambda item: int(item["sequence"]))["gm_name"])

    chains: dict[str, list[str | None | BranchSpec]] = {}
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
                    gm_chain_list: list[Any] = [gm_chain]
                elif isinstance(gm_chain, Sequence) and not isinstance(gm_chain, (str, bytes)):
                    gm_chain_list = list(gm_chain)
                else:
                    raise ValueError(
                        f"flow_to_gms['{flow_name}'] must be a string or a list of GM names "
                        "and/or {branch: ...} nodes."
                    )
                # An entry is a GM name, ``None``/empty (an "empty slot": the flow idles
                # that stage — only meaningful under multi_gm_staged), or a {branch: ...}
                # node that fans the flow's agents across several GMs at one stage.
                resolved: list[str | None | BranchSpec] = []
                branch_count = 0
                for entry in gm_chain_list:
                    if isinstance(entry, (Mapping, DictConfig)):
                        resolved.append(_parse_branch(flow_name, entry, gm_names))
                        branch_count += 1
                    elif entry is None:
                        resolved.append(None)
                    else:
                        resolved.append(str(entry).strip() or None)
                if branch_count > 1:
                    raise ValueError(
                        f"flow_to_gms['{flow_name}'] has {branch_count} branch nodes; at most "
                        "one branch per chain is supported."
                    )
                # Trailing empty slots have no effect, so trim them.
                while resolved and resolved[-1] is None:
                    resolved.pop()
                if not any(isinstance(item, (str, BranchSpec)) for item in resolved):
                    raise ValueError(f"flow_to_gms['{flow_name}'] cannot be empty.")
                unknown = [gm for gm in resolved if isinstance(gm, str) and gm not in gm_names]
                if unknown:
                    raise ValueError(f"Unknown GMs in flow_to_gms['{flow_name}']: {unknown}")
                chains[flow_name] = resolved
        elif flow_to_gms:
            raise ValueError("env.gm_orchestration.flow_bindings.flow_to_gms must be a mapping.")

    for flow in sorted(declared_flows):
        chains.setdefault(flow, [default_gm])
    chains.setdefault(DEFAULT_FLOW_TAG, [default_gm])

    for flow_name, gm_chain in chains.items():
        # Each non-idle position contributes a sequence band: a single GM is a point band
        # [seq, seq]; a branch spans [min, max] over its choices. Positions must be
        # strictly increasing band-to-band, so a branch occupies one stage that sits
        # cleanly between its neighbours (and the whole chain has no duplicate GM).
        positions: list[tuple[int, int]] = []
        all_names: list[str] = []
        for entry in gm_chain:
            names = flow_chain_gm_names([entry])
            if not names:
                continue
            seqs = [gm_sequences[name] for name in names]
            positions.append((min(seqs), max(seqs)))
            all_names.extend(names)
        if len(set(all_names)) != len(all_names):
            raise ValueError(f"Flow '{flow_name}' has duplicate GMs in chain: {gm_chain}")
        for (_, left_max), (right_min, _) in zip(positions, positions[1:], strict=False):
            if left_max >= right_min:
                raise ValueError(
                    "Flow chain must be strictly serial by sequence for multi-GM flows: "
                    f"flow='{flow_name}' chain={gm_chain}. "
                    "Ensure each subsequent GM (or branch) has a higher sequence number."
                )

    return chains


def resolve_flow_chains(cfg: DictConfig) -> dict[str, list[str | None | BranchSpec]]:
    """Resolve the flow -> GM-sequence routing topology from config.

    This is engine/scheduling config (the step strategy and the checkpoint replay
    router consume it), NOT game-master state. Exposed so callers that build the
    engine/restore path (``session.py``) can source the chains directly from config
    rather than introspecting them off a constructed game master. A chain entry is a GM
    name, ``None`` (an idle slot), or a :class:`BranchSpec` (a ``{branch: ...}`` node).
    """
    gm_specs = _resolve_gm_specs(cfg)
    declared_flows = _collect_declared_flow_tags(cfg)
    return _resolve_flow_chains(cfg, gm_specs, declared_flows)


def flow_chain_gm_names(chain: Sequence[Any]) -> list[str]:
    """Every concrete GM name a flow chain can route to, branch choices included.

    Used where a branch must be treated as "any of its choices": per-GM owned-flow
    derivation and the checkpoint-restore chain view.
    """
    names: list[str] = []
    for entry in chain:
        if isinstance(entry, BranchSpec):
            names.extend(entry.choices)
        elif isinstance(entry, str):
            names.append(entry)
    return names


def collapse_flow_chains(
    chains: Mapping[str, Sequence[Any]],
) -> dict[str, list[str | None]]:
    """Flatten branch nodes to their choices so non-routing consumers see plain names.

    The step strategy needs the rich chains (with ``BranchSpec`` routers); checkpoint
    restore only needs the set of GMs a flow can reach, so each branch is spread inline
    into its choices. Position is not meaningful to restore, so spreading is sufficient.
    """
    collapsed: dict[str, list[str | None]] = {}
    for flow, chain in chains.items():
        out: list[str | None] = []
        for entry in chain:
            if isinstance(entry, BranchSpec):
                out.extend(entry.choices)
            else:
                out.append(entry)
        collapsed[flow] = out
    return collapsed


def _parse_branch(flow_name: str, raw: Any, gm_names: set[str]) -> BranchSpec:
    """Parse a ``{branch: {router, choices}}`` chain node into a :class:`BranchSpec`.

    Validates only the static shape here (>= 2 known, distinct choices); the router is
    built later by the engine, and the branch's sequence band is checked against its
    chain neighbours by the caller.
    """
    data = OmegaConf.to_container(raw, resolve=True) if isinstance(raw, DictConfig) else dict(raw)
    if not isinstance(data, Mapping) or "branch" not in data:
        raise ValueError(
            f"flow_to_gms['{flow_name}'] chain entry must be a GM name, null, or a "
            f"{{branch: ...}} node; got {data!r}."
        )
    branch = dict(data.get("branch") or {})
    choices = tuple(str(c).strip() for c in (branch.get("choices") or []) if str(c).strip())
    if len(choices) < 2:
        raise ValueError(
            f"branch in flow_to_gms['{flow_name}'] needs at least 2 choices, got {list(choices)}."
        )
    if len(set(choices)) != len(choices):
        raise ValueError(f"branch in flow_to_gms['{flow_name}'] has duplicate choices: {choices}.")
    unknown = [c for c in choices if c not in gm_names]
    if unknown:
        raise ValueError(
            f"branch in flow_to_gms['{flow_name}'] references unknown GM(s): {unknown}."
        )
    router_slot = dict(branch.get("router") or {})
    return BranchSpec(choices=choices, router_slot=router_slot)


def build_game_masters(cfg: DictConfig) -> list[GameMasterConfig]:
    validate_runtime_structure(cfg)

    gm_specs = _resolve_gm_specs(cfg)
    declared_flows = _collect_declared_flow_tags(cfg)
    flow_chains = _resolve_flow_chains(cfg, gm_specs, declared_flows)

    projection = RuntimeProjection.from_cfg(cfg)
    # Build each GM's backend config, then isolate per-GM paths and reject db-path
    # collisions before constructing GMs (so same-type GMs can't clobber each other).
    backend_configs = [
        (
            str(spec["gm_name"]),
            _backend_config(cfg, spec["backend"], path=str(spec["backend_path"])),
        )
        for spec in gm_specs
    ]
    _isolate_backend_paths(backend_configs)
    backend_config_by_gm = dict(backend_configs)

    game_masters: list[GameMasterConfig] = []
    for spec in gm_specs:
        gm_name = str(spec["gm_name"])
        # owned_flows is the per-GM scalar derivation the GM still needs (component
        # routing); the full flow_chains topology is engine config and no longer
        # copied onto every GM. See resolve_flow_chains / the step strategy.
        owned_flows = [
            flow for flow, chain in flow_chains.items() if gm_name in flow_chain_gm_names(chain)
        ]
        gm_components_cfg = dict(spec["components"])
        action_prompt_params = dict(
            dict(gm_components_cfg.get("action_prompt", {}) or {}).get("params", {}) or {}
        )
        # Per-GM overrides fall back to the global projection when unset (today's behavior).
        eff_action_mode = normalize_action_mode(spec.get("action_mode")) or projection.action_mode
        eff_tool_calling_mode = (
            normalize_tool_calling_mode(spec.get("tool_calling_mode"))
            or projection.tool_calling_mode
        )
        resolve_built_in = str(
            dict(gm_components_cfg.get("resolve", {}) or {}).get("built_in") or "parsed_action"
        )
        validate_resolve_tool_calling(
            tool_calling_mode=eff_tool_calling_mode,
            resolve_built_in=resolve_built_in,
            resolve_path=str(spec["resolve_path"]),
        )
        action_prompt = _build_action_prompt(
            cfg,
            action_mode=eff_action_mode,
            tool_calling_mode=eff_tool_calling_mode,
            action_prompt_params=action_prompt_params,
        )
        gm_components_cfg["initialize"] = dict(spec["initializer"])
        gm_params = {
            "name": gm_name,
            "backend_config": backend_config_by_gm[gm_name],
            "components": gm_components_cfg,
            "action_prompt_template": action_prompt,
            "action_mode": eff_action_mode,
            "tool_calling_mode": eff_tool_calling_mode,
            "sim_roles": {},
            "agent_flow_tags": {},
            "owned_flows": owned_flows,
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
