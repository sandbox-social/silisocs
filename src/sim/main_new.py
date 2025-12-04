import concurrent.futures
import importlib
import logging
import os
import sys
import warnings
from pathlib import Path

import concordia.prefabs.entity as entity_prefabs
import concordia.prefabs.game_master as game_master_prefabs
import hydra

# @title Imports
from concordia import __file__ as concordia_location
from concordia.prefabs.simulation import generic as simulation
from concordia.typing import prefab as prefab_lib
from concordia.utils import helper_functions
from dotenv import load_dotenv
from omegaconf import DictConfig, OmegaConf, open_dict

print(f"importing Concordia from: {concordia_location}")
warnings.filterwarnings(action="ignore", category=FutureWarning, module="concordia")

# concordia functions

# Go up two levels to set current working directory to project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
print("project root: " + str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# mastodon_sim functions
from mastodon_sim.mastodon_ops import check_env, clear_mastodon_server
from sim.agent_utils.social_media_game_master import SocialMediaGM
from sim.sim_utils.agent_speech_utils import (
    write_seed_toot,
)
from sim.sim_utils.concordia_utils_new import (
    create_agent_instances_from_config,
    set_up_mastodon_app_usage,
)

# sim functions
from sim.sim_utils.media_utils import select_large_language_model
from sim.sim_utils.misc_sim_utils import (
    ConfigStore,
    EventLogger,
    StdoutToLogger,
    get_sentence_encoder,
)
from sim.sim_utils.social_media_engine import SocialMediaEngine


def post_seed_toots(agents, mastodon_apps):
    # Parallelize the loop using ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor() as executor:
        # Submit tasks for each agent
        futures = [
            executor.submit(
                lambda agent=agent: (
                    mastodon_apps[agent._agent_name].post_toot(
                        agent._agent_name, status=agent.seed_toot
                    )
                    if hasattr(agent, "seed_toot")
                    else mastodon_apps[agent._agent_name].post_toot(
                        agent._agent_name, status=write_seed_toot(agent)
                    )
                )
            )
            for agent in agents
        ]

        # Optionally, wait for all tasks to complete
        for future in concurrent.futures.as_completed(futures):
            future.result()  # This will raise any exceptions that occurred in the thread, if any


def configure_logging(logger):
    # supress verbose printing of hydra's api logging so only warnings (or greater issues) are printed
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Redirect stdout to the logger
    sys.stdout = StdoutToLogger(logger)


@hydra.main(version_base=None, config_path="../../conf", config_name="config")
def main(cfg: DictConfig):
    OmegaConf.set_struct(cfg, True)
    with open_dict(cfg):
        # Construct output_rootname using os.path.join for platform independence
        cfg.sim.output_rootname = os.path.join(
            hydra.core.hydra_config.HydraConfig.get().runtime.output_dir,
            hydra.core.hydra_config.HydraConfig.get().job.name,
        )
    os.makedirs(cfg.sim.output_rootname, exist_ok=True)
    # make cfg globally accessible through ConfigStore import
    ConfigStore.set_config(cfg)

    logger = logging.getLogger(__name__)
    configure_logging(logger)

    package = importlib.import_module(cfg.sim.example_name)
    sys.modules["sim_setting"] = package

    if cfg.sim.use_server:
        check_env()
        clear_mastodon_server(len(cfg.agents.directory))
    else:
        input("Sim will not use the Mastodon server. Confirm by pressing any key to continue.")

    load_dotenv(PROJECT_ROOT)

    # SEED = cfg.sim.seed
    # random.seed(SEED)

    # load language models
    model = select_large_language_model(
        cfg.sim.model,
        os.path.join(cfg.sim.output_rootname, "prompts_and_responses.jsonl"),
        True,
    )
    embedder = get_sentence_encoder(cfg.sim.sentence_encoder)

    # @title Load prefabs from packages to make the specific palette to use here.
    prefabs = {
        **helper_functions.get_package_classes(entity_prefabs),
        **helper_functions.get_package_classes(game_master_prefabs),
    }
    cfg = ConfigStore.get_config()
    num_episodes = cfg.sim.num_episodes
    use_server = cfg.sim.use_server
    call_to_action = cfg.soc_sys.call_to_action
    setting_info = cfg.soc_sys.setting_info

    shared_memories = (
        cfg.soc_sys.shared_agent_memories_template
        + [cfg.soc_sys.setting_info.description]
        + [cfg.soc_sys.social_media_usage_instructions]
    )
    role_parameters = setting_info["details"]["role_parameters"]
    app_description = cfg.soc_sys.social_media_usage_instructions

    # Create Agents
    agent_data = OmegaConf.to_container(cfg.agents.directory, resolve=True)
    if not isinstance(agent_data, list):
        raise TypeError(f"Expected cfg.agents.director to be a list, but got {type(agent_data)}")

    (
        entity_agent_list,
        exogenous_agent_list,
        roles,
        entity_player_names,
        player_specific_memories_map,
        player_specific_context_map,
        prefab_agents_map,
    ) = create_agent_instances_from_config(agent_data, prefabs)

    action_event_logger = EventLogger(
        "action", os.path.join(cfg.sim.output_rootname, "action_events.jsonl")
    )
    action_event_logger.episode_idx = -1
    mastodon_app, active_rates, user_mapping = set_up_mastodon_app_usage(
        roles, role_parameters, action_event_logger, app_description, use_server
    )

    # Add Game Masters
    # Add Configurator
    configurator = prefab_lib.InstanceConfig(
        prefab="formative_memories_initializer__GameMaster",
        role=prefab_lib.Role.INITIALIZER,
        params={
            "name": "initial setup rules",
            "next_game_master_name": "social media",
            "shared_memories": shared_memories,
            "player_specific_memories": player_specific_memories_map,
            "player_specific_context": player_specific_context_map,
        },
    )
    # SM Game Master
    prefab_agents_map["SocialMedia__GameMaster"] = SocialMediaGM()
    social_gm = prefab_lib.InstanceConfig(
        prefab="SocialMedia__GameMaster",
        role=prefab_lib.Role.GAME_MASTER,
        params={
            "name": "social media",
            "call_to_action_str": call_to_action,
            "sm_app_data": user_mapping,
            "user_server": use_server,
            "app_description": app_description,
            "output_path": cfg.sim.output_rootname,
            "active_rates": active_rates,
            "action_logger": action_event_logger,
        },
    )
    # Survey Game Master
    # Convert to questionnaires
    # probe_event_logger = EventLogger(
    #     "probe", os.path.join(cfg.sim.output_rootname, "probe_events.jsonl")
    # )
    # probes_config = OmegaConf.to_container(cfg.probes, resolve=True)
    # questionnaires, query_questionnaire = create_interviewer_gm_with_queries(
    #     probes_config=probes_config,
    #     player_names=entity_player_names
    # )
    # prefab_agents_map["interviewer__GameMaster"] = game_master_prefabs.interviewer.GameMaster()
    # interviewer_gm = prefab_lib.InstanceConfig(
    #     prefab="interviewer__GameMaster",
    #     role=prefab_lib.Role.GAME_MASTER,
    #     params={
    #         "name": "InterviewerGM",
    #         "player_names": entity_player_names,
    #         "questionnaires": questionnaires,  # Your converted queries
    #         "verbose": False,
    #     },
    # )

    # instances = entity_agent_list + exogenous_agent_list + [configurator, social_gm]
    instances = entity_agent_list + [
        configurator,
        social_gm,
    ]  # , interviewer_gm]  # TODO: Set-up exogenous agents
    # Set-up Config
    config = prefab_lib.Config(
        default_premise="",
        default_max_steps=120,
        prefabs=prefab_agents_map,
        instances=instances,
    )

    # Run Simulation

    sim_engine = SocialMediaEngine()
    runnable_simulation = simulation.Simulation(
        config=config,
        model=model,
        embedder=embedder,
        engine=sim_engine,
    )

    # @title Run the simulation
    num_episodes = 2
    results_log = runnable_simulation.play(max_steps=num_episodes)
    print(cfg.sim.output_rootname)


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT / "examples"))
    main()  # config)
