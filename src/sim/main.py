# src/sim/main.py
"""
Main simulation entry point.
Uses Hydra for configuration - works directly with YAML structure.
"""

import logging
import os
import random
import sys
import warnings
from dataclasses import asdict
from pathlib import Path

import concordia.prefabs.entity as entity_prefabs
import concordia.prefabs.game_master as game_master_prefabs
import hydra

# Concordia imports
from concordia import __file__ as concordia_location
from concordia.prefabs.simulation import generic as simulation
from concordia.typing import prefab as prefab_lib
from concordia.utils import helper_functions

# Environment
from dotenv import find_dotenv, load_dotenv
from omegaconf import DictConfig

# Local imports
from sim.config_utils.scenario_schema import validate_scenario_config
from sim.config_utils.simulation_dataclasses import (
    GameMasterConfig,
    InitializerConfig,
    InitializerParams,
    SimRole,
)
from sim.config_utils.social_media_dataclasses import SocialMediaParams, UserData
from sim.config_utils.social_media_functions import get_simrole_parameters
from sim.engines.social_media_engine import SocialMediaEngine
from sim.sim_utils.media_utils import select_large_language_model
from sim.sim_utils.misc_sim_utils import (
    ConfigStore,
    configure_logging,
    get_prefab_instance,
    get_sentence_encoder,
    write_concordia_logs,
)

# ============================================================================
# Startup Banner
# ============================================================================

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

# ============================================================================
# Project Setup
# ============================================================================

# Go up two levels to set current working directory to project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
print(f"Project root: {PROJECT_ROOT}")
print("=" * 80)
os.chdir(PROJECT_ROOT)

# Add path to source code
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pathlib import Path

print(Path("src/conf/config.yaml").exists())

CONF_DIR = PROJECT_ROOT / "src" / "conf"
print(f"Config directory: {CONF_DIR}")

# ============================================================================
# Helper Functions
# ============================================================================


def build_game_masters(cfg: DictConfig) -> list[prefab_lib.InstanceConfig]:
    """
    Build game master instances from YAML configuration.

    Args:
        cfg: Hydra configuration with social_media and scenario sections

    Returns
    -------
        List of game master instance configs
    """
    # Build shared memories
    shared_memories = list(cfg.scenario.shared_memories) + [cfg.social_media.usage_instructions]

    # Build player-specific context and memories from agents
    # (These will be populated after agents are created)
    player_specific_context: dict[str, str] = {}
    player_specific_memories: dict[str, list[str]] = {}

    # Create Initializer Game Master
    initializer_gm = InitializerConfig(
        prefab="formative_memories_initializer__GameMaster",
        params=asdict(
            InitializerParams(
                name="initial setup rules",
                next_game_master_name=f"{cfg.social_media.gamemaster.filename}__GameMaster",
                shared_memories=shared_memories,
                player_specific_memories=player_specific_memories,
                player_specific_context=player_specific_context,
            )
        ),
    )

    # Create Social Media Game Master
    sim_role = SimRole(
        name=cfg.social_media.gamemaster.sim_role.name,
        module_path=cfg.social_media.gamemaster.sim_role.module_path,
    )

    # Get social media role parameters
    simrole_params = get_simrole_parameters(
        active_rates=dict(cfg.scenario.social_network.active_rates),
        roles=list(cfg.scenario.social_network.active_rates.keys()),
        fully_connected_targets=list(cfg.scenario.social_network.fully_connected_targets),
        base_probability=cfg.scenario.social_network.base_followership_probability,
    )

    # Build sim_roles map (will be populated after agents are created)
    sim_roles: dict[str, str] = {}

    sm_user_data = UserData(
        sim_role_parameters=simrole_params,
        sim_roles=sim_roles,
    )

    social_media_gm = GameMasterConfig(
        prefab=f"{cfg.social_media.gamemaster.filename}__GameMaster",
        params=asdict(
            SocialMediaParams(
                name=cfg.social_media.gamemaster.name,
                calls_to_action={"social_media_action": cfg.social_media.action_call_to_action},
                sim_role=sim_role,
                app_module_path=cfg.sim.app_module_path,
                sm_user_data=sm_user_data,
                app_description=cfg.social_media.usage_instructions,
            )
        ),
    )

    return [initializer_gm, social_media_gm]


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
    # Build maps from agent configs
    sim_roles = {}
    player_specific_memories = {}
    player_specific_context = {}

    for agent in agent_configs:
        agent_name = agent.params["name"]
        sim_roles[agent_name] = agent.params["sim_role"]["name"]
        player_specific_memories[agent_name] = [""]
        player_specific_context[agent_name] = agent.params["context"]

    # Update Initializer GM
    initializer_gm = game_masters[0]
    initializer_gm.params["player_specific_memories"].update(player_specific_memories)
    initializer_gm.params["player_specific_context"].update(player_specific_context)

    # Update Social Media GM
    social_media_gm = game_masters[1]
    social_media_gm.params["sm_user_data"]["sim_roles"].update(sim_roles)


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

    # Setup Logging and Environment
    logger = logging.getLogger(__name__)

    # Load environment variables
    if load_dotenv(find_dotenv()):
        logger.info(f"Successfully loaded .env file from: {find_dotenv()}")
    else:
        logger.warning("Warning: .env file not found or empty.")

    configure_logging(logger)

    # Determine scenario path for file validation
    scenario_path = PROJECT_ROOT / "src" / "scenarios" / cfg.scenario.scenario_name

    # Run all config schema validation checks
    try:
        validate_scenario_config(cfg.scenario, scenario_path)
    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        raise

    # Add hydra-generated output path
    output_dir = os.path.join(
        hydra.core.hydra_config.HydraConfig.get().runtime.output_dir,
        hydra.core.hydra_config.HydraConfig.get().job.name,
    )

    # Update config with output directory
    cfg.sim.output_rootname = output_dir
    cfg.sim.scenario_name = cfg.scenario.scenario_name

    print(f"\nOutput directory: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)

    # build gamemasters (scenario agnostic)
    game_masters = build_game_masters(cfg)

    # Import scenario-specific agent builder and build agents
    builder_module_path = f"scenarios.{cfg.scenario.scenario_name}.builders"
    try:
        import importlib

        builder_module = importlib.import_module(builder_module_path)
        builder_class_name = f"{cfg.scenario.scenario_name.title()}AgentBuilder"
        BuilderClass = getattr(builder_module, builder_class_name)
    except (ImportError, AttributeError) as e:
        raise ImportError(
            f"Could not import builder from {builder_module_path}. "
            f"Expected class name: {builder_class_name}. Error: {e}"
        )
    builder = BuilderClass(cfg.scenario)
    agent_configs = builder.build_agents(cfg.scenario.roles)

    populate_agent_data(agent_configs, game_masters)

    SEED = cfg.sim.seed
    random.seed(SEED)
    print(f"\n✓ Random seed set to: {SEED}")

    instances = agent_configs + game_masters

    # Get concordia entity map
    concordia_entity_map = {
        **helper_functions.get_package_classes(entity_prefabs),
        **helper_functions.get_package_classes(game_master_prefabs),
    }

    # Get custom entity map
    custom_entity_map = {
        instance.prefab: get_prefab_instance(
            instance.prefab, instance.params["sim_role"]["module_path"]
        )
        for instance in instances
        if instance.prefab not in concordia_entity_map
    }

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
    model = select_large_language_model(cfg.sim.llm_name, prompts_file, True)

    embedder = get_sentence_encoder(cfg.sim.sentence_encoder)

    sim_engine = SocialMediaEngine()

    runnable_simulation = simulation.Simulation(
        config=concordia_config,
        model=model,
        embedder=embedder,
        engine=sim_engine,
    )

    results_log = runnable_simulation.play(max_steps=cfg.sim.num_steps)

    write_concordia_logs(results_log, output_dir)


if __name__ == "__main__":
    main()
