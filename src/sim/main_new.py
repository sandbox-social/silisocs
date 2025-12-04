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

# from sim.sim_utils.concordia_utils_new import (
#     create_agent_instances_from_config,
# )
# sim functions
from sim.sim_utils.media_utils import select_large_language_model
from sim.sim_utils.misc_sim_utils import (
    ConfigStore,
    StdoutToLogger,
    get_sentence_encoder,
)
from sim.sim_utils.social_media_engine import SocialMediaEngine


def create_agent_instances_from_config(
    agent_config_list: list[Any],
    prefabs: dict[str, Any],
) -> tuple[
    list[prefab_lib.InstanceConfig],
    list[prefab_lib.InstanceConfig],
    dict[str, str],
    dict[str, list[str]],
    dict[str, str],
    dict[str, Any],
]:
    """
    Processes agent configs, creating InstanceConfig objects and loading prefabs.

    - Sorts agents into 'entity' and 'exogenous' lists based on 'role_dict.name'.
    - Dynamically imports agent classes and returns a map of prefab_string -> class_instance.
    """
    entity_agent_list = []
    exogenous_agent_list = []
    player_specific_memories_map = {}
    player_specific_context_map = {}
    roles = {}
    entity_player_names = []
    prefab_agents_map: dict[str, Any] = prefabs

    for agent_data in agent_config_list:
        player_name = agent_data["name"]
        role_name = agent_data["role_dict"]["name"]
        roles[player_name] = role_name
        # --- a. Construct Prefab Name and Class Info ---
        if role_name != "exogenous":
            module_path_str = "sim_setting." + agent_data["role_dict"]["module_path"]
            # class_name_str = "AgentBuilder"
            # prefab_string = (
            #     f"{module_path_str.split('sim_setting.')[1].replace('.', '__')}__{class_name_str}"
            # )
            class_name_str = "Entity"
            prefab_string = "basic__Entity"

            # --- b. Load Prefab Class ---
            if prefab_string not in prefab_agents_map:
                print(f"[Loader] Loading prefab: {prefab_string}")
                try:
                    # e.g. importlib.import_module("sim_setting.agent_lib.voter")
                    buildagent_module = importlib.import_module(module_path_str)
                    # e.g., getattr(module, "AgentBuilder")
                    buildagent_class = getattr(buildagent_module, class_name_str)
                    # Store the *instantiated* class
                    prefab_agents_map[prefab_string] = buildagent_class()
                except ImportError:
                    print(f"Error: Could not import module: {module_path_str}")
                except AttributeError:
                    print(f"Error: Module {module_path_str} does not have class: {class_name_str}")
                except Exception as e:
                    print(f"An error occurred while loading prefab {prefab_string}: {e}")

            # --- c. Compress Context String ---
            context_parts = []
            if "context" in agent_data:
                context_parts.append(f"Biography: {agent_data['context']}")
            if "gender" in agent_data:
                context_parts.append(f"Gender: {agent_data['gender']}")
            if "style" in agent_data:
                context_parts.append(f"Communication Style: {agent_data['style']}")
            if "party" in agent_data:
                context_parts.append(f"Political Party: {agent_data['party']}")
            if "traits" in agent_data and isinstance(agent_data["traits"], dict):
                traits_str = ", ".join(f"{k}: {v}" for k, v in agent_data["traits"].items())
                context_parts.append(f"Traits: [{traits_str}]")
            compressed_context = "\n".join(context_parts)

            # --- d. Create the InstanceConfig ---
            agent_config = prefab_lib.InstanceConfig(
                prefab=prefab_string,
                role=prefab_lib.Role.ENTITY,
                params={
                    "name": player_name,
                    "goal": agent_data.get("goal", "Live a normal life."),
                    "context": compressed_context,
                },
            )

            # --- e. Sort agent and memory data based on role_dict.name ---

            entity_agent_list.append(agent_config)
            entity_player_names.append(player_name)
            original_memories = [agent_data.get("memories", "No specific memories.")]
            player_specific_memories_map[player_name] = original_memories
            player_specific_context_map[player_name] = compressed_context
        else:
            exogenous_agent_list.append(
                prefab_lib.InstanceConfig(
                    prefab="exogenous_agent__ExogenousAgent",
                    role=prefab_lib.Role.ENTITY,
                    params={"name": player_name},
                )
            )

    return (
        entity_agent_list,
        exogenous_agent_list,
        roles,
        player_specific_memories_map,
        player_specific_context_map,
        prefab_agents_map,
    )


def configure_logging(logger):
    # supress verbose printing of hydra's api logging so only warnings (or greater issues) are printed
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Redirect stdout to the logger
    sys.stdout = StdoutToLogger(logger)


def get_sm_agent_data(roles, role_parameters):
    active_rates = {}
    for agent_name, role in roles.items():
        active_rates[agent_name] = role_parameters["active_rates_per_episode"][role]
    # initiailize initial followership network randomly based on pair role follow probabilities
    follow_pairs = set()
    role_prob_matrix = role_parameters["initial_follow_prob"]
    for agent_i, role_i in roles.items():
        for agent_j, role_j in roles.items():
            if agent_i == agent_j:  # Agents cannot follow themselves
                continue
            prob = role_prob_matrix[role_i][role_j]
            if random.random() < prob:
                follow_pairs.add((agent_i, agent_j))
    return active_rates, follow_pairs


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

    # Get Agent Entities list
    agent_data = OmegaConf.to_container(cfg.agents.directory, resolve=True)
    if not isinstance(agent_data, list):
        raise TypeError(f"Expected cfg.agents.director to be a list, but got {type(agent_data)}")

    (
        entity_agent_list,
        exogenous_agent_list,
        roles,
        player_specific_memories_map,
        player_specific_context_map,
        prefab_agents_map,
    ) = create_agent_instances_from_config(agent_data, prefabs)

    # Get Game Master Entities list
    entity_game_master_list = []

    # Configurator
    shared_memories = (
        cfg.soc_sys.shared_agent_memories_template
        + [cfg.soc_sys.setting_info.description]
        + [cfg.soc_sys.social_media_usage_instructions]
    )
    entity_game_master_list.append(
        prefab_lib.InstanceConfig(
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
    )

    # SM Game Master
    user_mapping = {agent_name.split()[0]: f"user{i + 1:04d}" for i, agent_name in enumerate(roles)}
    active_rates, follow_pairs = get_sm_agent_data(
        roles, cfg.soc_sys.setting_info["details"]["role_parameters"]
    )
    prefab_agents_map["SocialMedia__GameMaster"] = SocialMediaGM()
    entity_game_master_list.append(
        prefab_lib.InstanceConfig(
            prefab="SocialMedia__GameMaster",
            role=prefab_lib.Role.GAME_MASTER,
            params={
                "name": "social media",
                "call_to_action_str": cfg.soc_sys.call_to_action,
                "sm_app_data": user_mapping,
                "user_server": cfg.sim.use_server,
                "app_description": cfg.soc_sys.social_media_usage_instructions,
                "output_path": cfg.sim.output_rootname,
                "active_rates": active_rates,
                "init_follow_pairs": follow_pairs,
            },
        )
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

    exogenous_agent_list = []  # remove once exogeneous agents set up
    instances = entity_agent_list + exogenous_agent_list + entity_game_master_list

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
    results_log = runnable_simulation.play(max_steps=cfg.sim.num_episodes)


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT / "examples"))
    main()  # config)
