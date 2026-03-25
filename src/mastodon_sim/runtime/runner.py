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
"""

import json
import logging
import os
import random
import sys
import time
import warnings
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import concordia.prefabs.entity as entity_prefabs
import concordia.prefabs.game_master as game_master_prefabs
import hydra

# Concordia imports
from concordia import __file__ as concordia_location
from concordia.typing import prefab as prefab_lib
from concordia.utils import helper_functions

# Environment
from dotenv import find_dotenv, load_dotenv
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf, open_dict

from mastodon_sim.environments.engines.social_media import (
    BaseSocialMediaEngine,
    FlowSocialMediaEngine,
)

# Local imports
from mastodon_sim.runtime.config import ConfigStore, validate_scenario_config
from mastodon_sim.runtime.dataclasses import (
    GameMasterConfig,
    InitializerConfig,
    InitializerParams,
    SimRole,
)
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
EXTERNAL_CONFIG_DIR_ENV = "MASTODON_SIM_EXTERNAL_CONFIG_DIR"


def _collect_explicit_override_paths(package_name: str) -> set[str]:
    """Collect explicit CLI override paths for a top-level package.

    Examples captured for package_name="sim":
      - sim.enabled_actions=[...]
      - +sim.engine.action_loop.params.max_actions=25
      - sim=base
    """
    explicit_paths: set[str] = set()
    for arg in sys.argv[1:]:
        if "=" not in arg:
            continue
        key = arg.split("=", 1)[0].strip()
        # Hydra allows +/++/~ prefixes for override semantics.
        key = key.lstrip("+~")
        if key == package_name or key.startswith(f"{package_name}."):
            explicit_paths.add(key)
    return explicit_paths


def _extract_external_package_overrides(
    external_dir: Path,
    filename: str,
    package_name: str,
) -> dict[str, Any] | None:
    """Load a root-level external override file and return its package payload.

    Supports either:
      1) @package style files (payload at root), or
      2) nested files where payload is under package_name key.
    """
    override_path = external_dir / filename
    if not override_path.is_file():
        return None

    raw_cfg: Any = OmegaConf.load(override_path)
    payload: Any = raw_cfg
    if isinstance(raw_cfg, DictConfig) and package_name in raw_cfg:
        nested_payload = raw_cfg.get(package_name)
        if isinstance(nested_payload, (DictConfig, Mapping)):
            payload = nested_payload

    payload_container = OmegaConf.to_container(payload, resolve=False)
    if not isinstance(payload_container, Mapping):
        return None
    return {str(key): value for key, value in payload_container.items()}


def _merge_mapping_with_override_protection(
    target: DictConfig,
    overrides: Mapping[str, Any],
    explicit_paths: set[str],
    prefix: str,
) -> None:
    """Merge mapping overrides into target, skipping explicit CLI override paths."""
    if prefix in explicit_paths:
        return

    for key, value in overrides.items():
        full_key = f"{prefix}.{key}"
        if full_key in explicit_paths:
            continue

        current_value = target.get(key)
        if isinstance(value, Mapping) and isinstance(current_value, DictConfig):
            _merge_mapping_with_override_protection(
                current_value,
                value,
                explicit_paths,
                full_key,
            )
            continue

        target[key] = value


def _apply_external_root_overrides(cfg: DictConfig, logger: logging.Logger) -> None:
    """Merge external root-level overrides (sim.yaml, social_media.yaml).

    This complements Hydra searchpath overrides by handling scenario files that
    live at the external config root rather than inside config groups.
    """
    external_dir_raw = os.environ.get(EXTERNAL_CONFIG_DIR_ENV)
    if not external_dir_raw:
        return

    external_dir = Path(external_dir_raw)
    if not external_dir.is_dir():
        logger.warning("External config dir is not a directory: %s", external_dir)
        return

    package_to_file = {
        "sim": "sim.yaml",
        "social_media": "social_media.yaml",
    }

    for package_name, filename in package_to_file.items():
        package_cfg = cfg.get(package_name)
        if not isinstance(package_cfg, DictConfig):
            continue

        overrides = _extract_external_package_overrides(
            external_dir,
            filename,
            package_name,
        )
        if not isinstance(overrides, dict):
            continue

        explicit_paths = _collect_explicit_override_paths(package_name)
        with open_dict(cfg):
            _merge_mapping_with_override_protection(
                package_cfg,
                overrides,
                explicit_paths,
                package_name,
            )

        logger.info(
            "Applied external %s overrides from %s",
            package_name,
            external_dir / filename,
        )


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


def _default_gm_filename(cfg: DictConfig, mode: str) -> str:
    """Resolve default GM prefab filename from preset/mode."""
    sim_cfg = getattr(cfg, "sim", object())
    gm_preset = str(getattr(getattr(sim_cfg, "gm", object()), "preset", "base") or "base")
    if mode == "shared" and gm_preset == "shared_flow":
        return "shared_flow_game_master"
    return str(cfg.social_media.gamemaster.filename)


def _default_gm_module_path(cfg: DictConfig, mode: str) -> str:
    """Resolve default GM module path from preset/mode."""
    sim_cfg = getattr(cfg, "sim", object())
    gm_preset = str(getattr(getattr(sim_cfg, "gm", object()), "preset", "base") or "base")
    if mode == "shared" and gm_preset == "shared_flow":
        return "mastodon_sim.environments.gm.shared_flow_game_master"
    return str(cfg.social_media.gamemaster.sim_role.module_path)


def _collect_declared_flow_tags(cfg: DictConfig) -> set[str]:
    """Collect flow tags declared at class level in the scenario config."""
    declared: set[str] = set()
    classes_cfg = (
        getattr(getattr(cfg.scenario, "persona_pipeline", object()), "classes", None)
        if hasattr(cfg, "scenario")
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


def _build_action_prompt(cfg: DictConfig, enable_tool_calling: bool) -> str:
    """Build action call-to-action prompt from config components.

    Args:
        cfg: Hydra config with social_media section
        enable_tool_calling: Whether tool-calling mode is enabled

    Returns
    -------
        Complete action call-to-action prompt string
    """
    action_prompt = getattr(cfg.social_media, "action_prompt", "")
    output_style = getattr(cfg.social_media, "output_style", "")

    if not action_prompt:
        return ""

    # When tool-calling is enabled, replace output_style with tool call instruction
    if enable_tool_calling:
        output_style = (
            "Respond with ONE of the available tool calls using this JSON format:\n"
            '{"tool_call": {"name": "<action_name>", "arguments": {...}}}'
        )

    return f"{action_prompt}\n\n{output_style}" if output_style else action_prompt


def _resolve_gm_specs(cfg: DictConfig) -> list[dict[str, Any]]:
    """Resolve GM specs from orchestration config."""
    default_gm = cfg.social_media.gamemaster
    default_mode = "shared"
    default_spec = {
        "gm_name": str(default_gm.name),
        "filename": _default_gm_filename(cfg, default_mode),
        "sim_role_name": str(default_gm.sim_role.name),
        "sim_role_module_path": _default_gm_module_path(cfg, default_mode),
        "sequence": 0,
        "mode": default_mode,
        "backend_scope": "shared_default",
    }

    sim_cfg = getattr(cfg, "sim", object())
    gm_specs_raw = getattr(getattr(sim_cfg, "gm_orchestration", object()), "gms", None)
    if not isinstance(gm_specs_raw, list) or not gm_specs_raw:
        return [default_spec]

    specs: list[dict[str, Any]] = []
    for idx, gm_raw in enumerate(gm_specs_raw):
        if not isinstance(gm_raw, Mapping):
            raise ValueError(f"sim.gm_orchestration.gms[{idx}] must be a mapping.")
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
            gm_raw.get("filename", _default_gm_filename(cfg, str(spec["mode"]))) or ""
        ).strip()
        spec.update(
            {
                "sim_role_name": str(
                    sim_role_cfg.get("name", default_spec["sim_role_name"]) or ""
                ).strip(),
                "sim_role_module_path": str(
                    sim_role_cfg.get("module_path", _default_gm_module_path(cfg, str(spec["mode"])))
                    or ""
                ).strip(),
                "sequence": int(gm_raw.get("sequence", idx)),
                "backend_scope": str(
                    gm_raw.get("backend_scope", "shared_default") or "shared_default"
                ).strip(),
            }
        )
        if not spec["gm_name"]:
            raise ValueError(f"sim.gm_orchestration.gms[{idx}] is missing gm_name/name.")
        specs.append(spec)

    names = [str(spec["gm_name"]) for spec in specs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate GM names in sim.gm_orchestration.gms: {duplicates}")
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
    bindings = getattr(getattr(sim_cfg, "gm_orchestration", object()), "flow_bindings", None)
    if isinstance(bindings, Mapping):
        gm_to_flows = bindings.get("gm_to_flows", {})
        if isinstance(gm_to_flows, Mapping):
            for gm_name, flow_values in gm_to_flows.items():
                gm_name_str = str(gm_name).strip()
                if gm_name_str not in gm_names:
                    raise ValueError(f"Unknown GM in gm_to_flows: {gm_name_str}")
                if isinstance(flow_values, str):
                    flow_iter = [flow_values]
                elif isinstance(flow_values, list):
                    flow_iter = flow_values
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
                elif isinstance(gm_chain, list):
                    gm_chain_list = gm_chain
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
        cfg: Hydra configuration with social_media and scenario sections

    Returns
    -------
        List of game master instance configs
    """
    # Build shared memories. Class-pipeline scenarios may define defaults under
    # persona_pipeline instead of top-level shared_memories.
    scenario_shared = OmegaConf.select(cfg, "scenario.shared_memories")
    if scenario_shared is None:
        scenario_shared = OmegaConf.select(
            cfg, "scenario.persona_pipeline.defaults.shared_memories"
        )
    shared_memories = list(scenario_shared or []) + [cfg.social_media.usage_instructions]
    processing_mode_raw = (
        cfg.scenario.persona_pipeline.processing_mode
        if hasattr(cfg.scenario, "persona_pipeline")
        and hasattr(cfg.scenario.persona_pipeline, "processing_mode")
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
    activity_transition_rates = dict(cfg.scenario.social_network.activity_transition_rates)
    fully_connected_targets = list(
        OmegaConf.select(cfg, "scenario.social_network.fully_connected_targets") or []
    )
    simrole_params = get_simrole_parameters(
        activity_transition_rates=activity_transition_rates,
        roles=list(activity_transition_rates.keys()),
        fully_connected_targets=fully_connected_targets,
        base_probability=cfg.scenario.social_network.base_followership_probability,
    )

    # Build sim_roles map (will be populated after agents are created)
    sim_roles: dict[str, str] = {}

    sm_user_data = UserData(
        sim_role_parameters=simrole_params,
        sim_roles=sim_roles,
    )

    social_media_gms: list[prefab_lib.InstanceConfig] = []
    sim_cfg = getattr(cfg, "sim", None)
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
                                enable_tool_calling=(
                                    getattr(
                                        getattr(
                                            getattr(sim_cfg, "gm", object()),
                                            "components",
                                            object(),
                                        ),
                                        "resolve",
                                        {},
                                    ).get("built_in")
                                    == "tool_calling"
                                ),
                            )
                        },
                        sim_role=sim_role,
                        app_module_path=getattr(cfg.social_media, "app_module_path", ""),
                        sm_user_data=gm_user_data,
                        app_description=cfg.social_media.usage_instructions,
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


def _build_engine(cfg: DictConfig):
    """Build runtime engine from config preset.

    `base` is the default and runs a single active social GM per episode with
    no flow-phase orchestration. `flow` enables flow/multi-GM orchestration.
    """
    engine_preset = str(getattr(getattr(cfg.sim, "engine", object()), "preset", "base") or "base")
    if engine_preset == "flow":
        return FlowSocialMediaEngine()
    if engine_preset == "base":
        return BaseSocialMediaEngine()
    raise ValueError(f"Unsupported sim.engine.preset='{engine_preset}'. Use 'base' or 'flow'.")


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
    _apply_external_root_overrides(cfg, logger)

    # Determine scenario path for file validation.
    # Check top-level scenarios/ first, fall back to in-package.
    project_root = PACKAGE_ROOT.parents[2]
    top_scenario = project_root / "scenarios" / cfg.scenario.scenario_name
    pkg_scenario = PACKAGE_ROOT / "scenarios" / cfg.scenario.scenario_name
    scenario_path = top_scenario if top_scenario.is_dir() else pkg_scenario

    # Run all config schema validation checks
    with metrics.phase("config_validation"):
        try:
            validate_scenario_config(cfg.scenario, scenario_path)
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
    cfg.sim.scenario_name = cfg.scenario.scenario_name
    OmegaConf.set_struct(cfg, True)

    print(f"\nOutput directory: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    run_stats_path = os.path.join(output_dir, "run_stats.log")

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
        import importlib
        import importlib.util

        from mastodon_sim.agents.builders import BaseAgentBuilder

        scenario_name = cfg.scenario.scenario_name
        builder_class_name = f"{scenario_name.title()}AgentBuilder"
        BuilderClass = None

        # 1. Try in-package builder.
        try:
            mod = importlib.import_module(f"mastodon_sim.scenarios.{scenario_name}.builders")
            BuilderClass = getattr(mod, builder_class_name, None)
        except (ImportError, ModuleNotFoundError):
            pass

        # 2. Try external scenarios/<name>/builders.py.
        if BuilderClass is None:
            from pathlib import Path

            pkg_root = Path(__file__).resolve().parents[1]
            project_root = pkg_root.parents[2]
            external_builder = project_root / "scenarios" / scenario_name / "builders.py"
            if external_builder.is_file():
                spec = importlib.util.spec_from_file_location(
                    f"scenarios.{scenario_name}.builders",
                    external_builder,
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    BuilderClass = getattr(mod, builder_class_name, None)

        # 3. Fallback to generic base builder.
        if BuilderClass is None:
            BuilderClass = BaseAgentBuilder

        builder = BuilderClass(cfg.scenario)
        roles = dict(cfg.scenario.roles) if hasattr(cfg.scenario, "roles") else {}
        agent_configs = builder.build_agents(roles)
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
    metrics.set_meta("scenario", cfg.scenario.scenario_name)
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

    sim_engine = _build_engine(cfg)

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

    **Auto-detection**: If the external dir contains a ``scenario/*.yaml``
    file, the scenario override (e.g. ``scenario=election``) is injected
    automatically — no need to pass it on the command line.
    """
    flag = "--config-path"
    if flag not in sys.argv:
        return

    idx = sys.argv.index(flag)
    if idx + 1 >= len(sys.argv):
        print(f"ERROR: {flag} requires a directory argument.")
        sys.exit(1)

    external_dir = Path(sys.argv[idx + 1]).resolve()
    if not external_dir.is_dir():
        print(f"ERROR: {flag} directory does not exist: {external_dir}")
        sys.exit(1)

    # Remove the flag and its argument so Hydra doesn't see them.
    del sys.argv[idx : idx + 2]
    os.environ[EXTERNAL_CONFIG_DIR_ENV] = str(external_dir)

    # Inject a file:// searchpath override so external configs are discoverable
    # while package defaults remain available as fallback.
    # List both paths: external first (higher priority), then package defaults
    external_search = f"file://{external_dir}"
    package_conf = str(CONF_DIR)
    package_search = f"file://{Path(package_conf).resolve()}"
    override = f"hydra.searchpath=[{external_search},{package_search}]"
    sys.argv.append(override)
    print(f"External config path: {external_dir}")

    # Auto-detect scenario name from scenario/*.yaml in the external dir,
    # unless the user already provided an explicit scenario= override.
    has_explicit_scenario = any(arg.startswith("scenario=") for arg in sys.argv[1:])
    if not has_explicit_scenario:
        scenario_dir = external_dir / "scenario"
        if scenario_dir.is_dir():
            yamls = [f.stem for f in scenario_dir.glob("*.yaml")]
            if len(yamls) == 1:
                sys.argv.append(f"scenario={yamls[0]}")
                print(f"Auto-detected scenario: {yamls[0]}")
            elif len(yamls) > 1:
                # Convention: use the one matching the parent directory name.
                parent_name = external_dir.parent.name
                if parent_name in yamls:
                    sys.argv.append(f"scenario={parent_name}")
                    print(f"Auto-detected scenario: {parent_name}")
                else:
                    print(
                        f"WARNING: Multiple scenario configs found ({yamls}) "
                        f"but none matches directory name '{parent_name}'. "
                        f"Pass scenario=<name> explicitly."
                    )


if __name__ == "__main__":
    _inject_external_config_path()
    main()
