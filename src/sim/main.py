import logging
import os
import random
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import concordia.prefabs.entity as entity_prefabs
import concordia.prefabs.game_master as game_master_prefabs
import hydra

# For development: import your current scenario for IDE support
# When adding a new scenario, just change this import
# To get scenario specific typing in a file, add this header:
#       if TYPE_CHECKING:
#           from scenarios.election.election import ScenarioConfig as CurrentScenarioConfig
#           from typing import cast
# and run:
#       cfg = ConfigStore.get_config()
#       if TYPE_CHECKING:
#           cfg.sc = cast(CurrentScenarioConfig, cfg.sc)
if TYPE_CHECKING:
    # TODO: Update this when working on a different scenario
    from scenarios.election.election import ScenarioConfig as CurrentScenarioConfig

# @title Imports
from concordia import __file__ as concordia_location
from concordia.prefabs.simulation import generic as simulation
from concordia.typing import prefab as prefab_lib
from concordia.utils import helper_functions
from dotenv import find_dotenv, load_dotenv

print(r"""
   _____ ____   __  ______  ____  ____ __  __   _____ ____  _____ ____ ___   __
  / ___//   |  / | / / __ \/ __ )/ __ \| |/ /  / ___// __ \/ ___//  _//   | / /
  \__ \/ /| | /  |/ / / / / __  | / / //   /  /___ \/ / / / /    / / / /| |/ /
 ___/ / ___ |/ /|  / /_/ / /_/ / /_/ //   |   ___/ / /_/ / /____/ / / ___ | /__
/____/_/  |_/_/ |_/_____/_____/\____//_/|_|  /____/\____/\____/___//_/  /_/___/
""")
print("=" * 50)
print(f"importing Concordia from: {concordia_location}")
warnings.filterwarnings(action="ignore", category=FutureWarning, module="concordia")
print("=" * 50)

# Go up two levels to set current working directory to project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
print("project root: " + str(PROJECT_ROOT))
print("=" * 50)
os.chdir(PROJECT_ROOT)

# Add path to source code
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

# sim functions
# Register and load configuration for a specific example from config_schema.py
from sim.config_utils.config_schema import ExperimentConfig, register_configs
from sim.engines.social_media_engine import SocialMediaEngine
from sim.sim_utils.media_utils import select_large_language_model
from sim.sim_utils.misc_sim_utils import (
    ConfigStore,
    configure_logging,
    get_prefab_instance,
    get_sentence_encoder,
    write_concordia_logs,
)

register_configs()


@hydra.main(version_base=None, config_name="config_schema")
def main(cfg: ExperimentConfig):
    """
    Main experiment function.

    Args:
        cfg: Structured configuration object
    """
    # Cast cfg.sc to typed version for both local use and global storage
    # This affects static type checking throughout the project
    if TYPE_CHECKING:
        from typing import cast

        # This tells type checkers that cfg.sc is now the concrete type
        cfg.sc = cast(CurrentScenarioConfig, cfg.sc)

    # Store config globally - now cfg.sc has proper typing for static analysis
    ConfigStore.set_config(cfg)

    # Use shorthand with full type info
    sc = cfg.sc

    # instantiate system logger and load environment variables from .env file
    logger = logging.getLogger(__name__)
    if load_dotenv(find_dotenv()):
        logger.info("Successfully loaded .env file from:" + find_dotenv())
    else:
        logger.warning("Warning: .env file not found or empty.")

    # give system logger to hydra to configure
    configure_logging(logger)

    # Use shorthand - now has full type info
    sc = cfg.sc

    print(f"Running experiment: {sc.soc_sys.exp_name}")
    print(f"Number of agents: {sc.sim.num_agents}")
    print(f"Number of steps: {sc.sim.num_steps}")
    print(f"Model: {sc.sim.llm_name}")
    print(f"Output directory: {sc.sim.output_rootname}")

    # set random seed
    SEED = sc.sim.seed
    random.seed(SEED)

    # load language models
    model = select_large_language_model(
        sc.sim.llm_name,
        os.path.join(sc.sim.output_rootname, "prompts_and_responses.jsonl"),
        True,
    )
    embedder = get_sentence_encoder(sc.sim.sentence_encoder)

    # load entity instances and build entity map
    entity_map = {
        **helper_functions.get_package_classes(entity_prefabs),
        **helper_functions.get_package_classes(game_master_prefabs),
    }

    instances = sc.agents.directory + sc.soc_sys.game_masters
    scenario_entity_map = {
        instance.prefab: get_prefab_instance(instance.prefab)
        for instance in instances
        if instance.prefab not in entity_map
    }
    entity_map = entity_map | scenario_entity_map

    # Instantiate simulation object config
    config = prefab_lib.Config(
        default_premise="",
        default_max_steps=120,
        prefabs=entity_map,
        instances=instances,
    )

    # Instantiate the simulation engine
    sim_engine = SocialMediaEngine()

    # Instantiate the Simulation
    runnable_simulation = simulation.Simulation(
        config=config,
        model=model,
        embedder=embedder,
        engine=sim_engine,
    )

    # Run the Simulation
    results_log = runnable_simulation.play(max_steps=sc.sim.num_steps)

    # write Concordia logs
    write_concordia_logs(results_log, sc.sim.output_rootname)


if __name__ == "__main__":
    main()
