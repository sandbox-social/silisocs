# src/mastodon_sim/runtime/runner.py
"""
Main simulation entry point.

Uses Hydra for configuration — works directly with YAML structure.

External scenarios override package defaults via ``--config-path``::

    python -m mastodon_sim.runtime.runner --config-path /abs/scenarios/election/conf
    python -m mastodon_sim.runtime.runner --config-path scenarios/election/conf

When ``--config-path`` is given the directory is prepended to Hydra's search
path so that any YAML files it contains override the corresponding package
defaults.  Missing files fall back to the package ``conf/`` directory.

Optional ``--overlay-config-path`` flags can be repeated to layer additional
override config trees on top of the primary ``--config-path``.
"""

import json
import logging
import os
import random
import sys
import time
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import concordia.prefabs.entity as entity_prefabs
import concordia.prefabs.game_master as game_master_prefabs
import hydra
import yaml

# Concordia imports
from concordia import __file__ as concordia_location
from concordia.typing import prefab as prefab_lib
from concordia.utils import helper_functions

# Environment
from dotenv import find_dotenv, load_dotenv
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf

from mastodon_sim.runtime.agent_building import build_agent_configs

# Local imports
from mastodon_sim.runtime.config import ConfigStore, validate_scenario_config
from mastodon_sim.runtime.dataclasses import (
    GameMasterConfig,
    InitializerConfig,
    InitializerParams,
    SimRole,
)
from mastodon_sim.runtime.factories import build_engine, default_gm_filename, default_gm_module_path
from mastodon_sim.runtime.projection import RuntimeProjection
from mastodon_sim.runtime.simulation import Simulation
from mastodon_sim.utils.media import select_large_language_model
from mastodon_sim.utils.misc import (
    SimMetricsCollector,
    configure_logging,
    get_prefab_instance,
    get_sentence_encoder,
    write_concordia_logs,
)
from mastodon_sim.utils.network import get_simrole_parameters
from mastodon_sim.utils.social_media_dataclasses import SocialMediaParams, UserData

# Package root (src/mastodon_sim)
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONF_DIR = PACKAGE_ROOT / "conf"


def _initialize_runtime_environment() -> Path:
    """Apply runtime setup only when the simulation entrypoint is executed."""
    print(r"""
   _____ ____   __  ______  ____  ____ __  __   _____ ____  _____ ____ ___   __
  / ___//   |  / | / / __ \/ __ )/ __ \| |/ /  / ___// __ \/ ___//  _//   | / /
  \__ \/ /| | /  |/ / / / / __  | / / //   /  /___ \/ / / / /    / / / /| |/ /
 ___/ / ___ |/ /|  / /_/ / /_/ / /_/ //   |   ___/ / /_/ / /____/ / / ___ | /__
/____/_/  |_/_/ |_/_____/_____/\____//_/|_|  /____/\____/\____/___//_/  /_/___/
""")
    print("=" * 80)
    print(f"Importing Concordia from: {concordia_location}")
    warnings.filterwarnings(action="ignore", category=FutureWarning, module="concordia")
    print("=" * 80)

    project_root = Path(__file__).resolve().parents[3]
    print(f"Project root: {project_root}")
    print("=" * 80)
    os.chdir(project_root)

    src_path = project_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    print(f"Config directory: {CONF_DIR}")
    return project_root


# ============================================================================
# Helper Functions
# ============================================================================

_DEFAULT_FLOW_TAG = "default"


def _env_cfg(cfg: Any) -> Any:
    return getattr(cfg, "env", getattr(cfg, "environment", object()))


def _evals_cfg(cfg: Any) -> Any:
    return getattr(cfg, "evals", getattr(cfg, "evaluations", object()))


def _collect_declared_flow_tags(cfg: DictConfig) -> set[str]:
    """Collect flow tags declared at class level in the scenario config."""
    declared: set[str] = set()
    classes_cfg = (
        getattr(getattr(cfg.agent, "persona_pipeline", object()), "classes", None)
        if hasattr(cfg, "agent")
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
    """Build the complete action prompt payload at runner startup."""
    from mastodon_sim.runtime.action_prompts import build_action_prompt_with_app_instance

    action_mode = str(getattr(cfg.sim, "action_mode", "custom") or "custom").strip().lower()
    # Generic prompts are built by game masters once backend app instances are available.
    if action_mode == "generic":
        return ""

    return build_action_prompt_with_app_instance(
        cfg=cfg,
        action_mode=action_mode,
        tool_calling_mode=tool_calling_mode,
        gm_prompt_cfg=gm_prompt_cfg,
    )


def _resolve_gm_specs(cfg: DictConfig) -> list[dict[str, Any]]:
    """Resolve GM specs from orchestration config."""
    default_gm = _env_cfg(cfg).gamemaster
    default_mode = "shared"
    default_spec = {
        "gm_name": str(default_gm.name),
        "filename": default_gm_filename(cfg, default_mode),
        "sim_role_name": str(default_gm.sim_role.name),
        "sim_role_module_path": default_gm_module_path(cfg, default_mode),
        "sequence": 0,
        "mode": default_mode,
        "backend_scope": "shared_default",
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
    """Resolve flow->GM chains with precedence: flow_to_gms > flow_to_gm > gm_to_flows."""
    if not gm_specs:
        return {}

    gm_names = {str(spec["gm_name"]) for spec in gm_specs}
    gm_sequences = {str(spec["gm_name"]): int(spec["sequence"]) for spec in gm_specs}
    default_gm = str(min(gm_specs, key=lambda item: int(item["sequence"]))["gm_name"])

    chains: dict[str, list[str]] = {}
    sim_cfg = getattr(cfg, "sim", object())
    gm_orchestration_cfg = getattr(_env_cfg(cfg), "gm_orchestration", None)
    if gm_orchestration_cfg is None:
        gm_orchestration_cfg = getattr(sim_cfg, "gm_orchestration", object())
    bindings = getattr(gm_orchestration_cfg, "flow_bindings", None)
    if isinstance(bindings, Mapping):
        gm_to_flows = bindings.get("gm_to_flows", {})
        if isinstance(gm_to_flows, Mapping):
            for gm_name, flow_values in gm_to_flows.items():
                gm_name_str = str(gm_name).strip()
                if gm_name_str not in gm_names:
                    raise ValueError(f"Unknown GM in gm_to_flows: {gm_name_str}")
                if isinstance(flow_values, str):
                    flow_iter = [flow_values]
                elif isinstance(flow_values, Sequence) and not isinstance(
                    flow_values, (str, bytes)
                ):
                    flow_iter = list(flow_values)
                else:
                    raise ValueError(
                        f"gm_to_flows['{gm_name_str}'] must be a string or list of strings."
                    )
                for flow in flow_iter:
                    flow_name = str(flow).strip()
                    if flow_name:
                        chains.setdefault(flow_name, [gm_name_str])

        flow_to_gm = bindings.get("flow_to_gm", {})
        if isinstance(flow_to_gm, Mapping):
            for flow, gm_name in flow_to_gm.items():
                flow_name = str(flow).strip()
                gm_name_str = str(gm_name).strip()
                if not flow_name:
                    continue
                if gm_name_str not in gm_names:
                    raise ValueError(f"Unknown GM in flow_to_gm['{flow_name}']: {gm_name_str}")
                chains[flow_name] = [gm_name_str]

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

    for flow in sorted(declared_flows):
        chains.setdefault(flow, [default_gm])

    if _DEFAULT_FLOW_TAG not in chains:
        chains[_DEFAULT_FLOW_TAG] = [default_gm]

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


def build_game_masters(cfg: DictConfig) -> list[prefab_lib.InstanceConfig]:
    """
    Build game master instances from YAML configuration.

    Args:
        cfg: Hydra configuration with grouped sim/agent/env/evals sections

    Returns
    -------
        List of game master instance configs
    """
    # Build shared memories. Class-pipeline scenarios may define defaults under
    # persona_pipeline instead of top-level shared_memories.
    scenario_shared = OmegaConf.select(cfg, "agent.shared_memories")
    if scenario_shared is None:
        scenario_shared = OmegaConf.select(cfg, "agent.persona_pipeline.defaults.shared_memories")
    shared_memories = list(scenario_shared or []) + [
        getattr(_env_cfg(cfg), "usage_instructions", "")
    ]
    processing_mode_raw = (
        cfg.agent.persona_pipeline.processing_mode
        if hasattr(cfg.agent, "persona_pipeline")
        and hasattr(cfg.agent.persona_pipeline, "processing_mode")
        else "formative"
    )
    processing_mode = str(processing_mode_raw).strip().lower()

    if processing_mode not in {"formative", "raw"}:
        raise ValueError(
            "Unsupported persona processing mode: "
            f"{processing_mode_raw}. Expected one of: `raw`, `formative`."
        )

    # Build player-specific context and memories from agents
    # (These will be populated after agents are created)
    player_specific_context: dict[str, str] = {}
    player_specific_memories: dict[str, list[str]] = {}
    initializer_prefab = "formative_memories_initializer__GameMaster"
    initializer_module_path = "mastodon_sim.agents.initialization.formative"
    if processing_mode == "raw":
        initializer_prefab = "raw_memories_initializer__GameMaster"
        initializer_module_path = "mastodon_sim.agents.initialization.raw"

    gm_specs = _resolve_gm_specs(cfg)
    declared_flows = _collect_declared_flow_tags(cfg)
    flow_chains = _resolve_flow_chains(cfg, gm_specs, declared_flows)

    sequence_entry = min(
        gm_specs,
        key=lambda item: (int(item["sequence"]), str(item["gm_name"])),
    )

    # Create Initializer Game Master
    initializer_gm = InitializerConfig(
        prefab=initializer_prefab,
        params=asdict(
            InitializerParams(
                name="initial setup rules",
                # Concordia next_game_master resolution is by *entity name*,
                # not by prefab id.
                next_game_master_name=str(sequence_entry["gm_name"]),
                shared_memories=shared_memories,
                player_specific_memories=player_specific_memories,
                player_specific_context=player_specific_context,
                module_path=initializer_module_path,
            )
        ),
    )

    # Get social media role parameters
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

    # Build sim_roles map (will be populated after agents are created)
    sim_roles: dict[str, str] = {}

    projection = RuntimeProjection.from_cfg(cfg)
    social_media_gms: list[prefab_lib.InstanceConfig] = []
    for spec in gm_specs:
        gm_name = str(spec["gm_name"])
        owned_flows = [flow for flow, chain in flow_chains.items() if gm_name in chain]
        sim_role = SimRole(
            name=str(spec["sim_role_name"]),
            module_path=str(spec["sim_role_module_path"]),
        )
        gm_user_data = UserData(
            sim_role_parameters=simrole_params,
            sim_roles=dict(sim_roles),
            gm_orchestration={
                "gm_name": gm_name,
                "sequence": int(spec["sequence"]),
                "mode": str(spec["mode"]),
                "backend_scope": str(spec["backend_scope"]),
                "owned_flows": owned_flows,
                "flow_chains": flow_chains,
                "prompt": dict(spec.get("prompt") or {}),
            },
        )
        social_media_gms.append(
            GameMasterConfig(
                prefab=f"{spec['filename']}__GameMaster",
                params=asdict(
                    SocialMediaParams(
                        name=gm_name,
                        # Determine if tool-calling is enabled
                        calls_to_action={
                            "social_media_action": _build_action_prompt(
                                cfg,
                                tool_calling_mode=projection.tool_calling_mode,
                                gm_prompt_cfg=cast(
                                    Mapping[str, Any] | None,
                                    spec.get("prompt"),
                                ),
                            )
                        },
                        sim_role=sim_role,
                        app_module_path=getattr(_env_cfg(cfg), "app_module_path", ""),
                        sm_user_data=gm_user_data,
                        app_description=getattr(_env_cfg(cfg), "usage_instructions", ""),
                    )
                ),
            )
        )

    return [initializer_gm, *social_media_gms]


def populate_agent_data(
    agent_configs: list[prefab_lib.InstanceConfig],
    game_masters: list[prefab_lib.InstanceConfig],
):
    """
    Populate game master parameters with agent-specific data.

    This modifies the game masters in-place to add player-specific
    memories and context after agents have been created.

    Args:
        agent_configs: List of agent configurations
        game_masters: List of game master configurations (will be modified)
    """

    def _normalize_memories(memories: Any) -> list[str]:
        if memories is None:
            return []
        if isinstance(memories, str):
            lines = [line.strip() for line in memories.splitlines() if line.strip()]
            if lines:
                return lines
            return [memories.strip()] if memories.strip() else []
        if isinstance(memories, list):
            return [str(item).strip() for item in memories if str(item).strip()]
        return [str(memories).strip()] if str(memories).strip() else []

    # Build maps from agent configs
    sim_roles: dict[str, str] = {}
    player_specific_memories: dict[str, list[str]] = {}
    player_specific_context: dict[str, str] = {}
    entity_flow_tags: dict[str, str] = {}
    extra_shared_memories: list[str] = []

    for agent in agent_configs:
        agent_name = agent.params["name"]
        sim_roles[agent_name] = agent.params["sim_role"]["name"]
        player_specific_memories[agent_name] = _normalize_memories(
            agent.params.get("specific_memories", [])
        )
        player_specific_context[agent_name] = str(agent.params.get("context", ""))
        flow_tag = str(agent.params.get("flow_tag", _DEFAULT_FLOW_TAG) or _DEFAULT_FLOW_TAG).strip()
        if not flow_tag:
            flow_tag = _DEFAULT_FLOW_TAG
        entity_flow_tags[agent_name] = flow_tag

        for memory in _normalize_memories(agent.params.get("shared_memories", [])):
            if memory not in extra_shared_memories:
                extra_shared_memories.append(memory)

    initializer_gm = next(
        (gm for gm in game_masters if gm.role == prefab_lib.Role.INITIALIZER), None
    )
    if initializer_gm:
        initializer_gm.params["player_specific_memories"].update(player_specific_memories)
        initializer_gm.params["player_specific_context"].update(player_specific_context)
        existing_shared_memories = _normalize_memories(
            initializer_gm.params.get("shared_memories", [])
        )
        for memory in extra_shared_memories:
            if memory not in existing_shared_memories:
                existing_shared_memories.append(memory)
        initializer_gm.params["shared_memories"] = existing_shared_memories

    social_media_gms = [gm for gm in game_masters if gm.role == prefab_lib.Role.GAME_MASTER]
    if not social_media_gms:
        raise ValueError("No social media game master found.")
    for social_media_gm in social_media_gms:
        user_data = social_media_gm.params.setdefault("sm_user_data", {})
        user_data.setdefault("sim_roles", {}).update(sim_roles)
        user_data["entity_flow_tags"] = dict(entity_flow_tags)

        orchestration = user_data.setdefault("gm_orchestration", {})
        owned_flows_raw = (
            orchestration.get("owned_flows", []) if isinstance(orchestration, dict) else []
        )
        owned_flows = {str(flow).strip() for flow in owned_flows_raw if str(flow).strip()}
        if isinstance(orchestration, dict) and owned_flows:
            orchestration["owned_entities"] = sorted(
                name for name, flow in entity_flow_tags.items() if flow in owned_flows
            )


def _build_legacy_scenario_view(cfg: DictConfig) -> DictConfig:
    """Build a scenario-like view for validators expecting legacy shape."""
    payload = {
        "scenario_name": OmegaConf.select(cfg, "sim.scenario_name"),
        "jobname_format": OmegaConf.select(cfg, "sim.jobname_format"),
        "setting": OmegaConf.select(cfg, "sim.setting") or {},
        "event": OmegaConf.select(cfg, "sim.event") or {},
        "data": OmegaConf.select(cfg, "sim.data") or {},
        "social_network": OmegaConf.select(cfg, "env.social_network")
        or OmegaConf.select(cfg, "sim.social_network")
        or {},
        "persona_pipeline": OmegaConf.select(cfg, "agent.persona_pipeline") or {},
        "shared_memories": OmegaConf.select(cfg, "agent.shared_memories") or [],
        "initial_observations": OmegaConf.select(cfg, "agent.initial_observations") or [],
        "probes": OmegaConf.select(cfg, "evals.probes")
        or OmegaConf.select(cfg, "evaluations.probes")
        or {},
        "seed_posts": OmegaConf.select(cfg, "env.seed_posts")
        or OmegaConf.select(cfg, "sim.seed_posts")
        or {},
        "fixed_action_sets": OmegaConf.select(cfg, "agent.fixed_action_sets")
        or OmegaConf.select(cfg, "sim.fixed_action_sets")
        or {},
        "candidates": OmegaConf.select(cfg, "env.candidates")
        or OmegaConf.select(cfg, "sim.candidates")
        or {},
        "news_account": OmegaConf.select(cfg, "env.news_account")
        or OmegaConf.select(cfg, "sim.news_account")
        or {},
        "partisan_types": OmegaConf.select(cfg, "env.partisan_types")
        or OmegaConf.select(cfg, "sim.partisan_types")
        or [],
    }
    return OmegaConf.create(payload)


def _merge_external_group_overrides(cfg: DictConfig) -> DictConfig:
    """Merge optional external group-level files from scenario config dirs."""
    paths_csv = os.environ.get("MASTODON_SIM_EXTERNAL_CONFIG_DIRS", "").strip()
    if not paths_csv:
        return cfg

    merged_cfg: DictConfig = cfg
    for raw_dir in [p for p in paths_csv.split(":") if p]:
        conf_dir = Path(raw_dir)
        for group, aliases in (
            ("agent", ("agent",)),
            ("sim", ("sim",)),
            ("env", ("env", "environment")),
            ("evals", ("evals", "evaluations")),
        ):
            for file_group in aliases:
                file_path = conf_dir / f"{file_group}.yaml"
                if not file_path.is_file():
                    continue
                loaded = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
                if not isinstance(loaded, dict):
                    raise ValueError(
                        f"Expected mapping in {file_path}, got {type(loaded).__name__}"
                    )
                merged_cfg = cast(
                    DictConfig,
                    OmegaConf.merge(merged_cfg, OmegaConf.create({group: loaded})),
                )
                break

    return merged_cfg


# ============================================================================
# Main Experiment Function
# ============================================================================


@hydra.main(version_base=None, config_path=str(CONF_DIR), config_name="config")
def main(cfg: DictConfig):
    """
    Main experiment function.

    Args:
        cfg: Hydra configuration object (composed from YAML files)
    """
    print("\n" + "=" * 80)
    print("STARTING SIMULATION")
    print("=" * 80)
    _initialize_runtime_environment()

    # Initialize metrics collector
    metrics = SimMetricsCollector.reset()
    metrics.mark_sim_start()

    # Setup Logging and Environment
    logger = logging.getLogger(__name__)

    # Load environment variables
    if load_dotenv(find_dotenv()):
        logger.info(f"Successfully loaded .env file from: {find_dotenv()}")
    else:
        logger.warning("Warning: .env file not found or empty.")

    configure_logging(logger)
    cfg = _merge_external_group_overrides(cfg)
    RuntimeProjection.from_cfg(cfg)

    # Determine scenario path for file validation.
    # Check top-level scenarios/ first, fall back to in-package.
    project_root = PACKAGE_ROOT.parents[2]
    top_scenario = project_root / "scenarios" / cfg.sim.scenario_name
    pkg_scenario = PACKAGE_ROOT / "scenarios" / cfg.sim.scenario_name
    scenario_path = top_scenario if top_scenario.is_dir() else pkg_scenario

    # Run all config schema validation checks
    with metrics.phase("config_validation"):
        try:
            validate_scenario_config(_build_legacy_scenario_view(cfg), scenario_path)
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            raise

    # Add hydra-generated output path
    output_dir = os.path.join(
        HydraConfig.get().runtime.output_dir,
        HydraConfig.get().job.name,
    )

    # Update config with output directory
    # Disable struct mode to allow setting new keys
    OmegaConf.set_struct(cfg, False)
    cfg.sim.output_rootname = output_dir
    cfg.sim.scenario_name = str(getattr(cfg.sim, "scenario_name", "default") or "default")
    OmegaConf.set_struct(cfg, True)

    print(f"\nOutput directory: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    run_stats_path = os.path.join(output_dir, "run_stats.log")

    # Persist runtime-effective config after external overrides are applied.
    effective_cfg_path = os.path.join(output_dir, "effective_config.yaml")
    OmegaConf.save(config=cfg, f=effective_cfg_path, resolve=True)
    logger.info("Wrote runtime-effective config to: %s", effective_cfg_path)

    # Mirror runtime-effective config next to Hydra's composed snapshot.
    # Hydra writes config.yaml under configs/<run_root_name>/ for this project.
    run_root_name = os.path.basename(HydraConfig.get().runtime.output_dir)
    config_snapshot_dir = os.path.join(
        HydraConfig.get().runtime.output_dir,
        "configs",
        run_root_name,
    )
    os.makedirs(config_snapshot_dir, exist_ok=True)
    effective_cfg_snapshot_path = os.path.join(config_snapshot_dir, "effective_config.yaml")
    OmegaConf.save(config=cfg, f=effective_cfg_snapshot_path, resolve=True)
    logger.info("Wrote runtime-effective config snapshot to: %s", effective_cfg_snapshot_path)

    def _log_startup_phase(phase_name: str, duration_s: float, details: str = "") -> None:
        details_part = f" {details}" if details else ""
        line = f"Startup {phase_name}: {duration_s:.2f}s{details_part}"
        logger.info(line)
        with open(run_stats_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # build gamemasters (scenario agnostic)
    t0 = time.time()
    with metrics.phase("build_game_masters"):
        game_masters = build_game_masters(cfg)
    _log_startup_phase("build_game_masters", time.time() - t0, f"count={len(game_masters)}")

    # Import scenario-specific agent builder and build agents.
    # Lookup order:
    #   1. In-package: mastodon_sim.scenarios.<name>.builders.<Name>AgentBuilder
    #   2. External:   scenarios/<name>/builders.py → <Name>AgentBuilder
    #   3. Fallback:   BaseAgentBuilder (YAML pipeline only)
    t0 = time.time()
    with metrics.phase("build_agents"):
        agent_configs = build_agent_configs(cfg)
    _log_startup_phase("build_agents", time.time() - t0, f"count={len(agent_configs)}")

    t0 = time.time()
    populate_agent_data(agent_configs, game_masters)
    social_gm_count = sum(1 for gm in game_masters if gm.role == prefab_lib.Role.GAME_MASTER)
    _log_startup_phase(
        "populate_agent_data",
        time.time() - t0,
        f"updated_game_masters={social_gm_count}",
    )

    SEED = cfg.sim.seed
    random.seed(SEED)
    print(f"\n✓ Random seed set to: {SEED}")

    instances = agent_configs + game_masters

    # Record metadata
    metrics.set_meta("num_agents", len(agent_configs))
    metrics.set_meta("num_game_masters", len(game_masters))
    metrics.set_meta("num_steps", cfg.sim.num_steps)
    metrics.set_meta("seed", SEED)
    metrics.set_meta("scenario", cfg.sim.scenario_name)
    metrics.set_meta("llm_name", cfg.sim.llm_name)
    metrics.set_meta("output_dir", output_dir)
    metrics.set_meta("agent_names", [inst.params["name"] for inst in agent_configs])

    # Get concordia entity map
    concordia_entity_map = {
        **helper_functions.get_package_classes(entity_prefabs),
        **helper_functions.get_package_classes(game_master_prefabs),
    }

    # Get custom entity map
    custom_entity_map = {}
    for instance in instances:
        if instance.prefab in concordia_entity_map:
            continue
        module_path = None
        if isinstance(instance.params, dict):
            if "sim_role" in instance.params and isinstance(instance.params["sim_role"], dict):
                module_path = instance.params["sim_role"].get("module_path")
            if not module_path:
                module_path = instance.params.get("module_path")
        if not module_path:
            raise ValueError(
                f"Custom prefab `{instance.prefab}` requires `sim_role.module_path` or `module_path` in params."
            )
        custom_entity_map[instance.prefab] = get_prefab_instance(instance.prefab, module_path)

    entity_map = concordia_entity_map | custom_entity_map

    # Create prefab config for Concordia
    concordia_config = prefab_lib.Config(
        default_premise="",
        default_max_steps=120,
        prefabs=entity_map,
        instances=instances,
    )

    # Store config globally
    ConfigStore.set_config(cfg)

    prompts_file = os.path.join(output_dir, "prompts_and_responses.jsonl")
    llm_api_base = getattr(cfg.sim, "llm_api_base", None) or None
    llm_api_key = getattr(cfg.sim, "llm_api_key", None) or None
    # Build models map and entity->model mapping for all instances.
    # If an instance doesn't specify a model name, default to `cfg.sim.llm_name`.
    t0 = time.time()
    with metrics.phase("model_creation"):
        models: dict[str, object] = {}
        entity_to_model: dict[str, str] = {}
        for instance in instances:
            try:
                model_name = instance.params["model"]["name"]
            except Exception:
                model_name = None

            if not model_name:
                model_name = cfg.sim.llm_name

            # map entity name to the model name
            entity_to_model[instance.params["name"]] = model_name

            # load the model once per unique name
            if model_name not in models:
                models[model_name] = select_large_language_model(
                    model_name,
                    prompts_file,
                    True,
                    disable_language_model=getattr(cfg.sim, "disable_language_model", False),
                    api_base=llm_api_base,
                    api_key=llm_api_key,
                    temperature=float(getattr(cfg.sim, "llm_temperature", 0.5)),
                )

        # Use the configured default model for compatibility (should be present in `models`).
        model = models.get(cfg.sim.llm_name)
        if model is None:
            model = select_large_language_model(
                cfg.sim.llm_name,
                prompts_file,
                True,
                disable_language_model=getattr(cfg.sim, "disable_language_model", False),
                api_base=llm_api_base,
                api_key=llm_api_key,
                temperature=float(getattr(cfg.sim, "llm_temperature", 0.5)),
            )
    _log_startup_phase("model_creation", time.time() - t0, f"unique_models={len(models)}")

    memory_backend = str(getattr(cfg.sim, "memory_backend", "associative")).strip().lower()
    t0 = time.time()
    if memory_backend == "list":
        embedder = None
        _log_startup_phase("embedder_creation", time.time() - t0, "skipped_for_memory_backend=list")
    else:
        with metrics.phase("embedder_creation"):
            embedder = get_sentence_encoder(cfg.sim.sentence_encoder)
        _log_startup_phase(
            "embedder_creation", time.time() - t0, f"encoder={cfg.sim.sentence_encoder}"
        )

    sim_engine = build_engine(cfg)

    t0 = time.time()
    with metrics.phase("simulation_construction"):
        runnable_simulation = Simulation(
            config=concordia_config,
            models=models,
            entity_to_model=entity_to_model,
            embedder=embedder,
            engine=sim_engine,
            memory_backend=memory_backend,
        )
    _log_startup_phase("simulation_construction", time.time() - t0)

    checkpoint_cfg = getattr(cfg.sim, "checkpoint", None)
    resume_file = None
    resume_step_override = None
    if checkpoint_cfg is not None:
        resume_file = getattr(checkpoint_cfg, "resume_file", None)
        resume_step_override = getattr(checkpoint_cfg, "resume_step", None)

    start_step = 0
    if resume_file:
        resume_path = Path(str(resume_file)).expanduser()
        if not resume_path.is_absolute():
            resume_path = (Path.cwd() / resume_path).resolve()
        if not resume_path.is_file():
            raise FileNotFoundError(f"Checkpoint file not found: {resume_path}")

        with metrics.phase("checkpoint_load"):
            with open(resume_path, encoding="utf-8") as f:
                checkpoint_data = json.load(f)
            runnable_simulation.load_from_checkpoint(checkpoint_data)

        default_step = checkpoint_data.get("step")
        if default_step is None:
            default_step = len(checkpoint_data.get("raw_log", []))
        start_step = int(default_step)

        if resume_step_override is not None:
            start_step = int(resume_step_override)

        logger.info(
            "Resumed from checkpoint %s at step %d",
            resume_path,
            start_step,
        )

    write_html_log = bool(getattr(cfg.sim, "write_html_log", True))
    checkpoint_output_dir = os.path.join(output_dir, "checkpoints")
    completion_status = "success"
    completion_error = ""
    try:
        t0 = time.time()
        with metrics.phase("simulation_play"):
            results_log = runnable_simulation.play(
                max_steps=cfg.sim.num_steps,
                start_step=start_step,
                checkpoint_path=checkpoint_output_dir,
                return_html_log=write_html_log,
            )
        _log_startup_phase(
            "simulation_play",
            time.time() - t0,
            f"max_steps={cfg.sim.num_steps} write_html_log={write_html_log}",
        )

        t0 = time.time()
        with metrics.phase("log_writing"):
            if write_html_log:
                write_concordia_logs(results_log, output_dir)
            else:
                logger.info("Skipping HTML log generation (sim.write_html_log=false).")
        _log_startup_phase("log_writing", time.time() - t0, f"write_html_log={write_html_log}")
    except Exception as e:
        completion_status = "failed"
        completion_error = str(e)
        logger.exception("Simulation execution failed before completion marker.")
        raise
    finally:
        # Finalize and write metrics
        metrics.mark_sim_end()
        metrics.write_json(output_dir)

        completion_line = (
            "Simulation complete: "
            f"status={completion_status} "
            f"episodes={cfg.sim.num_steps} "
            f"output_dir={output_dir}"
        )
        if completion_error:
            completion_line += f" error={completion_error}"
        logger.info(completion_line)
        print(completion_line)
        with open(run_stats_path, "a", encoding="utf-8") as f:
            f.write(completion_line + "\n")


def _inject_external_config_path() -> None:
    """Intercept ``--config-path <dir>`` from argv and add it to Hydra searchpath.

    Hydra's own ``--config-path`` replaces the *entire* primary config dir.
    We want additive behavior: the external dir overrides individual files
    while the package ``conf/`` provides fallback defaults.  So we consume
    the flag, resolve the path, and inject it as a ``hydra.searchpath``
    override instead.

    Optional ``--overlay-config-path <dir>`` flags can be repeated to layer
    extra override directories above the primary ``--config-path``.

    **Auto-detection**: If the external dir contains ``sim.yaml`` with
    ``scenario_name`` and/or ``jobname_format``, they are injected as CLI
    overrides so Hydra output paths are resolved correctly.
    """
    primary_flag = "--config-path"
    overlay_flag = "--overlay-config-path"
    if primary_flag not in sys.argv and overlay_flag not in sys.argv:
        return

    external_dir: Path | None = None
    overlay_dirs: list[Path] = []
    cleaned_argv = [sys.argv[0]]
    i = 1
    while i < len(sys.argv):
        token = sys.argv[i]
        if token in {primary_flag, overlay_flag}:
            if i + 1 >= len(sys.argv):
                print(f"ERROR: {token} requires a directory argument.")
                sys.exit(1)
            directory = Path(sys.argv[i + 1]).resolve()
            if not directory.is_dir():
                print(f"ERROR: {token} directory does not exist: {directory}")
                sys.exit(1)
            if token == primary_flag:
                external_dir = directory
            else:
                overlay_dirs.append(directory)
            i += 2
            continue
        cleaned_argv.append(token)
        i += 1

    if external_dir is None and not overlay_dirs:
        return

    sys.argv[:] = cleaned_argv
    search_parts = [f"file://{path}" for path in overlay_dirs]
    if external_dir is not None:
        search_parts.append(f"file://{external_dir}")
    package_conf = str(CONF_DIR)
    search_parts.append(f"file://{Path(package_conf).resolve()}")
    override = f"hydra.searchpath=[{','.join(search_parts)}]"
    sys.argv.append(override)
    if external_dir is not None:
        print(f"External config path: {external_dir}")
    for overlay in overlay_dirs:
        print(f"Overlay config path: {overlay}")

    # Persist external roots so `main()` can merge optional files:
    # agent.yaml, sim.yaml, env.yaml, evals.yaml
    merge_dirs: list[Path] = []
    if external_dir is not None:
        merge_dirs.append(external_dir)
    merge_dirs.extend(overlay_dirs)
    os.environ["MASTODON_SIM_EXTERNAL_CONFIG_DIRS"] = ":".join(str(path) for path in merge_dirs)

    # Inject sim metadata used by Hydra run-dir interpolation.
    if external_dir is not None:
        sim_file = external_dir / "sim.yaml"
        if sim_file.is_file():
            loaded = yaml.safe_load(sim_file.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                has_scenario = any(arg.startswith("sim.scenario_name=") for arg in sys.argv[1:])
                if not has_scenario and loaded.get("scenario_name"):
                    sys.argv.append(f"sim.scenario_name={loaded['scenario_name']}")

                has_jobname = any(arg.startswith("sim.jobname_format=") for arg in sys.argv[1:])
                if not has_jobname and loaded.get("jobname_format"):
                    value = str(loaded["jobname_format"]).replace('"', '\\"')
                    sys.argv.append(f'sim.jobname_format="{value}"')


if __name__ == "__main__":
    _inject_external_config_path()
    main()
