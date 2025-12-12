import importlib
import logging
import os
import random
import sys
import warnings
from pathlib import Path
from typing import Any

import concordia.prefabs.entity as entity_prefabs
import concordia.prefabs.game_master as game_master_prefabs
import hydra

# @title Imports
from concordia import __file__ as concordia_location
from concordia.prefabs.simulation import generic as simulation
from concordia.typing import prefab as prefab_lib
from concordia.utils import helper_functions
from dotenv import find_dotenv, load_dotenv
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf, open_dict

print(r"""
   _____ ____   __  ______  ____  ____ __  __   _____ ____  _____ ____ ___   __
  / ___//   |  / | / / __ \/ __ )/ __ \| |/ /  / ___// __ \/ ___//  _//   | / /
  \__ \/ /| | /  |/ / / / / __  | / / /|   /   \__ \/ / / / /    / / / /| |/ /
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
sys.path.insert(0, str(PROJECT_ROOT / "src"))


# sim functions
from sim.agent_utils.agent_instance_config import create_agent_instances_from_config
from sim.config_schema import register_configs
from sim.sim_utils.media_utils import select_large_language_model
from sim.sim_utils.misc_sim_utils import (
    ConfigStore,
    StdoutToLogger,
    get_sentence_encoder,
)
from sim.sim_utils.social_media_engine import SocialMediaEngine

# Specify which example to use
EXAMPLE_NAME = "election"
register_configs(EXAMPLE_NAME)


def configure_logging(logger):
    # supress verbose printing of hydra's api logging so only warnings (or greater issues) are printed
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Redirect stdout to the logger
    sys.stdout = StdoutToLogger(logger)


@hydra.main(version_base=None, config_path="../../conf", config_name="config")
def main(cfg: DictConfig):
    """
    Main experiment function.

    Args:
        cfg: Structured configuration object with full type hints
    """
    # Load config - enable struct mode for validation
    OmegaConf.set_struct(cfg, True)

    # Temporarily allow adding new fields to set output_rootname
    with open_dict(cfg):
        # Construct output_rootname using os.path.join for platform independence
        cfg.sim.output_rootname = os.path.join(
            HydraConfig.get().runtime.output_dir,
            HydraConfig.get().job.name,
        )

    # Create output directory
    os.makedirs(cfg.sim.output_rootname, exist_ok=True)

    # Make globally accessible with ConfigStore.get_config()
    # through "from sim.sim_utils.misc_sim_utils import ConfigStore"
    ConfigStore.set_config(cfg)

    # give system logger to hydra to configure
    logger = logging.getLogger(__name__)
    configure_logging(logger)

    print(f"Running experiment: {cfg.soc_sys.exp_name}")
    print(f"Number of agents: {cfg.sim.num_agents}")
    print(f"Number of episodes: {cfg.sim.num_episodes}")
    print(f"Model: {cfg.sim.model}")
    print(f"Output directory: {cfg.sim.output_rootname}")

    # set example package to be imported as "sim_setting"
    package = importlib.import_module(cfg.sim.example_name)
    sys.modules["sim_setting"] = package

    # load .env file with environment variables
    if load_dotenv(find_dotenv()):
        logger.info("Successfully loaded .env file from:" + find_dotenv())
    else:
        logger.warning("Warning: .env file not found or empty.")

    # set random seed
    SEED = cfg.sim.seed
    random.seed(SEED)

    # load language models
    model = select_large_language_model(
        cfg.sim.model,
        os.path.join(cfg.sim.output_rootname, "prompts_and_responses.jsonl"),
        True,
    )
    embedder = get_sentence_encoder(cfg.sim.sentence_encoder)

    # initialize the entity map to which key-item references to entities defined below will be added
    entity_map = {
        **helper_functions.get_package_classes(entity_prefabs),
        **helper_functions.get_package_classes(game_master_prefabs),
    }

    # Get Agent Entities list
    agent_data = OmegaConf.to_container(cfg.agents.directory, resolve=True)
    if not isinstance(agent_data, list):
        raise TypeError(f"Expected cfg.agents.director to be a list, but got {type(agent_data)}")
    (
        entity_agent_instance_list,
        exogenous_agent_instance_list,
        roles,
        player_specific_memories_map,
        player_specific_context_map,
        entity_map,
    ) = create_agent_instances_from_config(agent_data, entity_map)

    social_media_gm_name = cfg.sim.app_module + " social media"

    # Get Game Master Entities list
    entity_game_master_instance_list = []

    # Add Configurator Game Master
    shared_memories = (
        cfg.soc_sys.shared_agent_memories_template
        + [cfg.soc_sys.setting_info.description]
        + [cfg.soc_sys.social_media_usage_instructions]
    )
    entity_game_master_instance_list.append(
        prefab_lib.InstanceConfig(
            prefab="formative_memories_initializer__GameMaster",
            role=prefab_lib.Role.INITIALIZER,
            params={
                "name": "initial setup rules",
                "next_game_master_name": social_media_gm_name,
                "shared_memories": shared_memories,
                "player_specific_memories": player_specific_memories_map,
                "player_specific_context": player_specific_context_map,
            },
        )
    )

    # Add Social Media Game Master
    sm_user_data: dict[str, Any] = {
        "roles": roles,
        "role_parameters": cfg.soc_sys.setting_info.details.role_parameters,
    }
    gm_module = importlib.import_module(
        "sim.agent_utils." + cfg.sim.social_media_gamemaster_filename
    )
    entity_map[cfg.sim.social_media_gamemaster_filename + "__GameMaster"] = gm_module.GameMaster()
    entity_game_master_instance_list.append(
        prefab_lib.InstanceConfig(
            prefab=cfg.sim.social_media_gamemaster_filename + "__GameMaster",
            role=prefab_lib.Role.GAME_MASTER,
            params={
                "name": social_media_gm_name,
                "app_module": cfg.sim.app_module,
                "call_to_action_str": cfg.soc_sys.call_to_action,
                "sm_user_data": sm_user_data,
                "use_server": cfg.sim.use_server,
                "app_description": cfg.soc_sys.social_media_usage_instructions,
                "output_path": cfg.sim.output_rootname,
            },
        )
    )

    # Add Survey Game Master
    # Convert to questionnaires
    # probe_event_logger = EventLogger(
    #     "probe", os.path.join(cfg.sim.output_rootname, "probe_events.jsonl")
    # )
    # probes_config = OmegaConf.to_container(cfg.probes, resolve=True)
    # questionnaires, query_questionnaire = create_interviewer_gm_with_queries(
    #     probes_config=probes_config,
    #     player_names=entity_player_names
    # )
    # entity_map["survey_probe__GameMaster"] = game_master_prefabs.interviewer.GameMaster()
    # entity_game_master_list.append(
    #     prefab_lib.InstanceConfig(
    #         prefab="interviewer__GameMaster",
    #         role=prefab_lib.Role.GAME_MASTER,
    #         params={
    #             "name": "InterviewerGM",
    #             "player_names": entity_player_names,
    #             "questionnaires": questionnaires,  # Your converted queries
    #             "verbose": False,
    #         },
    #     )
    # )

    exogenous_agent_instance_list = []  # remove once exogeneous agents set up

    # Set-up Config
    config = prefab_lib.Config(
        default_premise="",
        default_max_steps=120,
        prefabs=entity_map,
        instances=entity_agent_instance_list
        + exogenous_agent_instance_list
        + entity_game_master_instance_list,
    )

    # Configure the Simulation
    sim_engine = SocialMediaEngine()
    runnable_simulation = simulation.Simulation(
        config=config,
        model=model,
        embedder=embedder,
        engine=sim_engine,
    )

    # Run the Simulation
    results_log = runnable_simulation.play(max_steps=cfg.sim.num_episodes)

    # write Concordia logs
    file_path = os.path.join(cfg.sim.output_rootname, "logs.html")
    try:
        with open(file_path, "w", encoding="utf-8") as html_file:
            html_file.write(results_log)
        print(f"HTML content successfully saved to {file_path}")
    except OSError as e:
        print(f"Error saving HTML content: {e}")


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT / "examples"))
    main()
