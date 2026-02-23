import concurrent.futures
import datetime
import importlib
import json
import logging
import os
import random
import sys
import time
import warnings
from functools import partial
from pathlib import Path

import hydra
from concordia import __file__ as concordia_location
from dotenv import load_dotenv
from omegaconf import DictConfig, OmegaConf, open_dict

from sim.agent_utils.base_agent import save_agent_to_json

print(f"importing Concordia from: {concordia_location}")
warnings.filterwarnings(action="ignore", category=FutureWarning, module="concordia")

# concordia functions
from concordia.clocks import game_clock
from concordia.typing.entity import ActionSpec, OutputType

# Go up two levels to set current working directory to project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
print("project root: " + str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# mastodon_sim functions
from mastodon_sim.mastodon_ops import check_env, clear_mastodon_server
from sim.sim_utils.agent_speech_utils import (
    deploy_probes,
)
from sim.sim_utils.concordia_utils import (
    build_agent_with_memories,
    generate_concordia_memory_objects,
    make_profiles,
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


def run_sim(
    model,
    embedder,
    output_post_analysis=False,
    save_checkpoints=False,
    load_from_checkpoint_path="",
):
    cfg = ConfigStore.get_config()
    app_description = cfg.soc_sys.social_media_usage_instructions
    episode_call_to_action = cfg.soc_sys.episode_call_to_action
    setting_info = cfg.soc_sys.setting_info
    num_episodes = cfg.sim.num_episodes
    use_server = cfg.sim.use_server

    time_step = datetime.timedelta(minutes=30)
    today = datetime.date.today()
    SETUP_TIME = datetime.datetime(year=today.year, month=today.month, day=today.day, hour=8)  # noqa: DTZ001
    START_TIME = datetime.datetime(year=today.year, month=today.month, day=today.day, hour=8)  # noqa: DTZ001
    clock = game_clock.MultiIntervalClock(
        start=SETUP_TIME, step_sizes=[time_step, datetime.timedelta(seconds=10)]
    )

    # set probe settings
    probes = OmegaConf.to_container(cfg.probes, resolve=True)

    # build agent models
    agent_data = OmegaConf.to_container(cfg.agents.directory, resolve=True)
    get_idx = lambda name: [ait for ait, agent in enumerate(agent_data) if agent["name"] == name][0]

    profiles, roles = make_profiles(agent_data)  # profile format: (agent_config,role)
    role_parameters = setting_info["details"]["role_parameters"]

    shared_memories = (
        cfg.soc_sys.shared_agent_memories_template
        + [cfg.soc_sys.setting_info.description]
        + [cfg.soc_sys.social_media_usage_instructions]
    )
    (
        importance_model,
        importance_model_gm,
        blank_memory_factory,
        formative_memory_factory,
        gamemaster_memory,
    ) = generate_concordia_memory_objects(
        model,
        embedder,
        shared_memories,
        cfg.soc_sys.gamemaster_memories,
        clock,
    )

    action_event_logger = EventLogger(
        "action", os.path.join(cfg.sim.output_rootname, "action_events.jsonl")
    )
    action_event_logger.episode_idx = -1

    mastodon_apps, phones, active_rates = set_up_mastodon_app_usage(
        roles, role_parameters, action_event_logger, app_description, use_server, setup_base=False
    )

    # Call replay_actions as a function if experiment_name is set, to populate server state
    if cfg.sim.experiment_name:
        action_events_file_path = os.path.join(
            PROJECT_ROOT,
            "outputs",
            hydra.core.hydra_config.HydraConfig.get().runtime.output_dir,
            cfg.sim.experiment_name,
            "action_events.jsonl",
        )
        replay_func_path = os.path.join(
            PROJECT_ROOT, "src", "sim", "scripts", "replay_actions_func.py"
        )
        if os.path.exists(action_events_file_path):
            print(
                f"Attempting to replay actions from: {action_events_file_path} using replay_actions_func"
            )
            try:
                import importlib.util
                import sys as _sys

                spec = importlib.util.spec_from_file_location(
                    "sim.scripts.replay_actions_func", replay_func_path
                )
                replay_mod = importlib.util.module_from_spec(spec)
                _sys.modules[spec.name] = replay_mod
                spec.loader.exec_module(replay_mod)

                # Determine episode/target_name for partial replay
                episode_to_continue = getattr(cfg.sim, "episode_to_continue", None)
                target_agent_name = getattr(cfg.sim, "target_agent_name", None)
                if episode_to_continue is not None and target_agent_name:
                    print(
                        f"Replaying up to episode {episode_to_continue}, before {target_agent_name}"
                    )
                    replay_mod.replay_actions(
                        action_events_file_path,
                        mastodon_apps,
                        episode_to_continue,
                        target_agent_name,
                    )
                elif episode_to_continue is not None:
                    print(f"Replaying up to episode {episode_to_continue}")
                    replay_mod.replay_actions(
                        action_events_file_path, mastodon_apps, episode_to_continue
                    )
                else:
                    replay_mod.replay_actions(action_events_file_path, mastodon_apps)
                print("Successfully replayed actions.")
            except Exception as e:
                print(f"Error occurred while executing replay_actions_func: {e}")
        else:
            print(
                f"Action events file not found at {action_events_file_path}. Skipping action replay."
            )

    # build agents
    agents = []
    local_post_analyze_data = {}
    obj_args = (formative_memory_factory, model, clock, time_step, setting_info)
    build_agent_with_memories_part = partial(build_agent_with_memories, obj_args)

    # Filter profiles if a target agent is specified
    target_agent_name = cfg.sim.target_agent_name
    if target_agent_name:
        print(f"Building only agent: {target_agent_name}")
        if target_agent_name not in profiles:
            raise ValueError(f"Agent '{target_agent_name}' not found in configuration")
        filtered_profiles = {target_agent_name: profiles[target_agent_name]}
    else:
        filtered_profiles = profiles

    action_spec = ActionSpec(
        call_to_action=episode_call_to_action,
        output_type=OutputType.FREE,
        tag="action",
    )

    # Experimental version (epsiode call to action and thought chains)
    online_gamemaster_module = importlib.import_module(
        "agent_utils." + cfg.sim.gamemasters.online_gamemaster
    )
    print(load_from_checkpoint_path)
    if load_from_checkpoint_path:
        filepath = os.path.join(load_from_checkpoint_path, f"{target_agent_name}.json")
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Checkpoint file not found: {filepath}")
        setting_data = {
            "setting_details": setting_info["details"],
            "setting_description": setting_info["description"],
        }
        with open(filepath) as file:
            agent_json = file.read()
        module_path = (
            "sim_setting." + filtered_profiles[target_agent_name]["role_dict"]["module_path"]
        )
        agent_module = importlib.import_module(module_path)
        agents.append(
            agent_module.AgentBuilder.rebuild_from_json(
                agent_json,
                model,
                embedder,
                clock,
                filtered_profiles[target_agent_name]["role_dict"] | setting_data,
            )
        )
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(filtered_profiles)) as pool:
            for agent_obj in pool.map(build_agent_with_memories_part, filtered_profiles.values()):
                agent, data = agent_obj
                agents.append(agent)
                local_post_analyze_data[agent._agent_name] = data
        # add agent-specific configuration
        for agent in agents:
            if roles[agent._agent_name] == "exogenous":
                # assign seed toots of exogenous agents with absolute path to images (if non empty)
                agent.seed_toot = agent_data[get_idx(agent._agent_name)]["seed_toot"]
                for post_text in agent.posts:
                    agent.posts[post_text] = [
                        str(PROJECT_ROOT) + "/" + path for path in agent.posts[post_text]
                    ]
            else:
                for observation in cfg.agents.initial_observations:
                    agent.observe(observation.format(name=agent._agent_name))
    env = online_gamemaster_module.GameMaster(
        model=model,
        memory=gamemaster_memory,
        phones=phones,
        clock=clock,
        agents=agents,
        roles=roles,
        action_spec=action_spec,
        memory_factory=blank_memory_factory,
        embedder=embedder,
        importance_model=importance_model,
        importance_model_gm=importance_model_gm,
    )  # initialize
    probe_event_logger = EventLogger(
        "probe", os.path.join(cfg.sim.output_rootname, "probe_events.jsonl")
    )

    # main loop
    start_time = time.time()  # Start timing
    model.agent_names = [
        agent._agent_name for agent in agents
    ]  # needed for tagging names to thoughts
    for i in range(num_episodes):
        action_event_logger.episode_idx = i
        model.meta_data["episode_idx"] = i
        probe_event_logger.episode_idx = i
        env.log_data = []

        print(f"Episode: {i}. Deploying survey...", end="")
        deploy_probes(
            [agent for agent in agents if roles[agent._agent_name] != "exogenous"],
            probes,
            probe_event_logger,
        )
        print("complete")

        if cfg.sim.target_agent_name:
            active_agent_names = [cfg.sim.target_agent_name]
        else:
            active_agent_names = env.get_active_agents(active_rates)

        if len(active_agent_names) == 0:
            clock.advance()
        else:
            start_timex = time.time()
            env.step(active_agents=active_agent_names)
            action_event_logger.log(env.log_data)

            end_timex = time.time()
            with open(
                os.path.join(cfg.sim.output_rootname, "episode_runtime_logger.txt"),
                "a",
            ) as f:
                f.write(
                    f"Episode with {len(active_agent_names)} finished - took {end_timex - start_timex}\\n"
                )  # save checkpoints
        if save_checkpoints:
            print("SAVING CHECKPOINTS")
            for agent_input, agent in zip(agent_data, agents, strict=False):
                # Skip if this is not our target agent (when specified) or if it's an exogenous agent
                if roles[agent._agent_name] == "exogenous":
                    continue
                if cfg.sim.target_agent_name and agent._agent_name != cfg.sim.target_agent_name:
                    continue

                print(f"Saving agent {agent._agent_name}")
                agent_dir = os.path.join(
                    cfg.sim.output_rootname, "agent_checkpoints", f"Episode_{i}"
                )
                os.makedirs(agent_dir, exist_ok=True)
                file_path = os.path.join(agent_dir, f"{agent._agent_name}.json")
                json_data = save_agent_to_json(agent)
                with open(file_path, "w") as file:
                    file.write(json.dumps(json_data, indent=4))

    if output_post_analysis:
        # post_analysis is currently disabled in misc_sim_utils
        # post_analysis(env, model, agents, roles, local_post_analyze_data, cfg.sim.output_rootname)
        pass


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

        if "target_agent_name" not in cfg.sim:
            cfg.sim.target_agent_name = None
        if "experiment_name" not in cfg.sim:
            cfg.sim.experiment_name = None

        if cfg.sim.target_agent_name:
            print(f"Running simulation for agent: {cfg.sim.target_agent_name}")
        else:
            print("Running simulation for all agents")

        if cfg.sim.experiment_name:
            print(f"Using experiment: {cfg.sim.experiment_name}")
            base_path = os.path.join(
                PROJECT_ROOT,
                "outputs",
                hydra.core.hydra_config.HydraConfig.get().runtime.output_dir,
                cfg.sim.experiment_name,
                "agent_checkpoints",
            )

            potential_load_path = (
                base_path  # Default if experiment_name points directly to an episode
            )

            if os.path.exists(base_path) and any(
                d.startswith("Episode_")
                for d in os.listdir(base_path)
                if os.path.isdir(os.path.join(base_path, d))
            ):
                episode_dirs = [
                    d
                    for d in os.listdir(base_path)
                    if d.startswith("Episode_") and os.path.isdir(os.path.join(base_path, d))
                ]
                if episode_dirs:
                    # Try to find the episode number n if specified, else default to latest
                    episode_n = cfg.sim.episode_to_continue
                    if episode_n is not None:
                        episode_dir = f"Episode_{episode_n}"
                        if episode_dir in episode_dirs:
                            last_episode = episode_dir
                        else:
                            print(f"Episode_{episode_n} not found, defaulting to latest episode.")
                            last_episode = sorted(episode_dirs, key=lambda x: int(x.split("_")[1]))[
                                -1
                            ]
                    else:
                        last_episode = sorted(episode_dirs, key=lambda x: int(x.split("_")[1]))[-1]
                    potential_load_path = os.path.join(base_path, last_episode)
                    print(potential_load_path)
                else:
                    pass  # potential_load_path remains base_path

            if os.path.exists(potential_load_path) and any(
                f.endswith(".json") for f in os.listdir(potential_load_path)
            ):
                cfg.sim.load_path = potential_load_path
                print(f"Loading from checkpoint: {cfg.sim.load_path}")
            else:
                print(
                    f"Checkpoint path not found or invalid: {potential_load_path}. Check sim.experiment_name or directory structure."
                )
                # Decide if sim.load_path should be None or raise an error
                cfg.sim.load_path = ""  # Or None, depending on how run_sim handles it

        elif (
            "load_path" not in cfg.sim or not cfg.sim.load_path
        ):  # if experiment_name is not given, ensure load_path is empty or not set
            cfg.sim.load_path = ""

    os.makedirs(cfg.sim.output_rootname, exist_ok=True)
    # make cfg globally accessible through ConfigStore import
    ConfigStore.set_config(cfg)

    logger = logging.getLogger(__name__)
    configure_logging(logger)

    package = importlib.import_module(cfg.sim.example_name)
    sys.modules["sim_setting"] = (
        package  # WARNING: Make sure no one else is running a sim before setting to True since this clears the server!
    )
    if cfg.sim.use_server:
        check_env()
        # If we're simulating a single agent, we still need to create all accounts
        # but we'll only build the target agent
        clear_mastodon_server(len(cfg.agents.directory))
    else:
        input("Sim will not use the Mastodon server. Confirm by pressing any key to continue.")

    load_dotenv(PROJECT_ROOT)

    SEED = cfg.sim.seed
    random.seed(SEED)

    # load language models
    model = select_large_language_model(
        cfg.sim.model, os.path.join(cfg.sim.output_rootname, "prompts_and_responses.jsonl"), True
    )
    embedder = get_sentence_encoder(cfg.sim.sentence_encoder)

    # run sim
    run_sim(
        model,
        embedder,
        load_from_checkpoint_path=cfg.sim.load_path,
    )


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT / "examples"))
    main()  # config)
